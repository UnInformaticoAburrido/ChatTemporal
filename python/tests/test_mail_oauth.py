import asyncio
import json
import smtplib
from unittest.mock import MagicMock
from urllib.parse import parse_qs

import pytest

from chat.errors import APIError
from chat.mail_config import load_mail_config
from chat.mail_oauth import NoRedirect, gmail_access_token
from chat.mailer import SMTPMailer


def configured(tmp_path, monkeypatch, **changes):
    values = dict(enabled=True, host='smtp.gmail.com', port=587, security='starttls',
                  sender='sender@example.com', auth_method='oauth2', client_id='synthetic-client',
                  client_secret='synthetic-secret', refresh_token='synthetic-refresh')
    values.update(changes)
    path = tmp_path / 'mail.local.toml'
    path.write_text('\n'.join(f'{k} = {json.dumps(v)}' for k, v in values.items()))
    monkeypatch.setenv('CHAT_MAIL_CONFIG_FILE', str(path))
    return load_mail_config()


def test_refresh_request_is_fixed_https_and_body_only(tmp_path, monkeypatch):
    config = configured(tmp_path, monkeypatch)
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(
        dict(access_token='synthetic-access', token_type='Bearer', expires_in=3600)).encode()
    monkeypatch.setattr('chat.mail_oauth.urllib.request.build_opener', lambda *args: opener)
    assert gmail_access_token(config) == 'synthetic-access'
    request = opener.open.call_args.args[0]
    assert request.full_url == 'https://oauth2.googleapis.com/token'
    assert parse_qs(request.data.decode()) == {
        'grant_type': ['refresh_token'], 'client_id': ['synthetic-client'],
        'client_secret': ['synthetic-secret'], 'refresh_token': ['synthetic-refresh']}
    assert 'synthetic' not in repr(config)
    assert 'synthetic' not in config.smtp_url()


@pytest.mark.parametrize('security,port', [('starttls', 587), ('tls', 465)])
def test_gmail_xoauth2_is_after_tls_and_never_uses_password(tmp_path, monkeypatch, security, port):
    configured(tmp_path, monkeypatch, security=security, port=port)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr('chat.mailer.smtplib.SMTP', MagicMock(return_value=connection))
    monkeypatch.setattr('chat.mailer.smtplib.SMTP_SSL', MagicMock(return_value=connection))
    monkeypatch.setattr('chat.mailer.gmail_access_token', lambda config: 'synthetic-access')
    asyncio.run(SMTPMailer('sender@example.com').verification('recipient@example.com', 'verification-code'))
    mechanism, callback = connection.auth.call_args.args
    assert mechanism == 'XOAUTH2'
    assert callback() == 'user=sender@example.com\x01auth=Bearer synthetic-access\x01\x01'
    assert callback(b'failure-challenge') == ''
    connection.login.assert_not_called()
    calls = [c[0] for c in connection.mock_calls]
    assert calls.index('auth') < calls.index('send_message')
    if security == 'starttls':
        assert calls.index('starttls') < calls.index('auth')
    else:
        connection.starttls.assert_not_called()


@pytest.mark.parametrize('failure', [ValueError('synthetic-secret'), OSError('synthetic-refresh'),
                                   smtplib.SMTPAuthenticationError(535, b'synthetic-access')])
def test_failed_refresh_or_auth_never_sends_or_falls_back(tmp_path, monkeypatch, failure):
    configured(tmp_path, monkeypatch)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr('chat.mailer.smtplib.SMTP', MagicMock(return_value=connection))
    monkeypatch.setattr('chat.mailer.gmail_access_token', MagicMock(side_effect=failure))
    with pytest.raises(APIError) as error:
        asyncio.run(SMTPMailer('sender@example.com').verification('recipient@example.com', 'code'))
    assert error.value.status == 503
    assert 'synthetic' not in str(error.value)
    connection.send_message.assert_not_called()
    connection.login.assert_not_called()


@pytest.mark.parametrize('payload', [[], {}, {'access_token': 'x'},
    {'access_token': 'x\n', 'token_type': 'Bearer'}, {'access_token': 'x', 'token_type': 'other'}])
def test_invalid_provider_response_is_rejected(tmp_path, monkeypatch, payload):
    config = configured(tmp_path, monkeypatch)
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    monkeypatch.setattr('chat.mail_oauth.urllib.request.build_opener', lambda *args: opener)
    with pytest.raises(ValueError):
        gmail_access_token(config)


@pytest.mark.parametrize('changes', [dict(host='smtp.other.com'), dict(password='not-used'),
    dict(client_secret=''), dict(refresh_token=''), dict(client_id=''), dict(auth_method='other')])
def test_invalid_oauth_config_is_rejected(tmp_path, monkeypatch, changes):
    with pytest.raises(ValueError):
        configured(tmp_path, monkeypatch, **changes)


def test_token_endpoint_redirect_is_rejected():
    with pytest.raises(ValueError):
        NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example.com')
