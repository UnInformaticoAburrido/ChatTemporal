import asyncio
import json
from unittest.mock import MagicMock
from urllib.parse import unquote, urlsplit

import pytest

from chat.config import load_settings, read_secret
from chat.mail_config import load_mail_config
from chat.mailer import SMTPMailer


def write_config(tmp_path, monkeypatch, **changes):
    values = dict(enabled=True, host='smtp.example.com', port=587, security='starttls',
                  sender='nexo@example.com', username='nexo@example.com', password='synthetic:@/#%value')
    values.update(changes)
    path = tmp_path / 'mail.local.toml'
    path.write_text('\n'.join(f'{key} = {json.dumps(value)}' for key, value in values.items()))
    monkeypatch.setenv('CHAT_MAIL_CONFIG_FILE', str(path))
    return path


def test_disabled_or_missing_preserves_legacy_smtp(local_settings, tmp_path, monkeypatch):
    legacy = read_secret('SMTP_URL')
    path = write_config(tmp_path, monkeypatch, enabled=False)
    assert load_mail_config() is None
    assert read_secret('SMTP_URL') == legacy
    path.unlink()
    assert load_mail_config() is None


@pytest.mark.parametrize('security,port', [('starttls', 587), ('tls', 465)])
def test_private_file_drives_sender_auth_and_tls(local_settings, tmp_path, monkeypatch, security, port):
    write_config(tmp_path, monkeypatch, security=security, port=port)
    monkeypatch.setenv('CHAT_LOCAL_MAILBOX', '1')
    settings = load_settings()
    assert settings.email_from == 'nexo@example.com'
    url = urlsplit(read_secret('SMTP_URL'))
    assert unquote(url.password) == 'synthetic:@/#%value'
    assert 'synthetic' not in repr(load_mail_config())
    connection = MagicMock()
    connection.__enter__.return_value = connection
    plain, tls = MagicMock(return_value=connection), MagicMock(return_value=connection)
    monkeypatch.setattr('chat.mailer.smtplib.SMTP', plain)
    monkeypatch.setattr('chat.mailer.smtplib.SMTP_SSL', tls)
    asyncio.run(SMTPMailer(settings.email_from).verification('recipient@example.com', 'synthetic-code'))
    used = plain if security == 'starttls' else tls
    assert used.call_args.args == ('smtp.example.com', port)
    assert connection.starttls.call_count == (1 if security == 'starttls' else 0)
    connection.login.assert_called_once_with('nexo@example.com', 'synthetic:@/#%value')
    message = connection.send_message.call_args.args[0]
    assert message['From'] == 'nexo@example.com'
    assert message['To'] == 'recipient@example.com'
    assert 'synthetic-code' in message.get_content()


@pytest.mark.parametrize('changes', [dict(security='none'), dict(port=0), dict(port=True),
    dict(sender='bad\nheader@example.com'), dict(password=''), dict(host='smtp.example.invalid'),
    dict(enabled='yes'), dict(unknown='value')])
def test_invalid_file_never_exposes_credentials_or_falls_back(tmp_path, monkeypatch, changes):
    write_config(tmp_path, monkeypatch, **changes)
    with pytest.raises(ValueError, match='Configuración de correo inválida') as error:
        read_secret('SMTP_URL')
    assert 'synthetic' not in str(error.value)


def test_malformed_toml_is_safe(tmp_path, monkeypatch):
    path = write_config(tmp_path, monkeypatch)
    path.write_text('password = "synthetic-secret\n')
    with pytest.raises(ValueError) as error:
        load_mail_config()
    assert 'synthetic-secret' not in str(error.value)
