import { QRCode, jsQR } from './vendor/qr.js';
import { TransferError } from './transfers.js';

export async function drawQR(canvas, text) {
  if (typeof text !== 'string' || text.length > 1024) throw new TransferError('TRANSFER_INVALID');
  await QRCode.toCanvas(canvas, text, { width: 280, margin: 4, errorCorrectionLevel: 'M' });
}
export function scannerMarkup(prefix, label) {
  return `<div id="${prefix}-scanner" class="qr-scanner">
    <label for="${prefix}-image">Imagen del QR ${label}</label><input id="${prefix}-image" type="file" accept="image/png,image/jpeg,image/webp">
    <button class="secondary" type="button" id="${prefix}-camera">Leer QR ${label} con cámara</button>
    <button class="secondary" type="button" id="${prefix}-stop" hidden>Detener cámara</button>
    <video id="${prefix}-video" aria-label="Vista de cámara" muted playsinline hidden></video>
    <p class="muted">La imagen se procesa en este dispositivo. También puedes pegar el código.</p></div>`;
}
export function mountScanner({ container, prefix, input, run, notify, isAlive }) {
  const $ = suffix => container.querySelector('#' + prefix + '-' + suffix);
  const video = $('video'), canvas = document.createElement('canvas');
  let stream = null, timer = null, stopped = false, generation = 0;
  const alive = () => !stopped && isAlive() && container.isConnected;
  function stopCamera() {
    generation++; clearTimeout(timer); timer = null;
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    video.pause(); video.srcObject = null; video.hidden = true;
    $('stop').hidden = true; $('camera').disabled = false;
    canvas.width = canvas.height = 0;
  }
  function readImage(source, width, height) {
    if (!width || !height) return null;
    const ratio = Math.min(1, 1024 / Math.max(width, height));
    canvas.width = Math.max(1, Math.round(width * ratio)); canvas.height = Math.max(1, Math.round(height * ratio));
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
    return jsQR(ctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height)?.data || null;
  }
  function accept(text) {
    if (text && text.length <= 1024 && alive()) {
      input.value = text; stopCamera(); notify('QR leído. Comprueba el dispositivo y continúa.'); return true;
    }
    return false;
  }
  $('image').onchange = () => {
    const file = $('image').files[0]; $('image').value = ''; stopCamera();
    const current = generation;
    run(async () => {
      if (!file || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 5 * 1024 * 1024) throw new TransferError('QR_NOT_FOUND');
      let bitmap;
      try {
        bitmap = await createImageBitmap(file);
        if (!alive() || generation !== current) return;
        if (!accept(readImage(bitmap, bitmap.width, bitmap.height))) throw new TransferError('QR_NOT_FOUND');
      } catch (error) { if (error instanceof TransferError) throw error; throw new TransferError('QR_NOT_FOUND'); }
      finally { bitmap?.close(); canvas.width = canvas.height = 0; }
    });
  };
  $('camera').onclick = () => run(async () => {
    stopCamera(); const current = generation;
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw Error();
      const capture = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: 'environment', width: 640, height: 480 } });
      if (!alive() || generation !== current) { capture.getTracks().forEach(track => track.stop()); return; }
      stream = capture; video.srcObject = stream; video.hidden = false;
      $('stop').hidden = false; $('camera').disabled = true; await video.play();
      function tick() {
        if (!alive() || !stream || generation !== current) return;
        try { if (video.readyState >= 2 && accept(readImage(video, video.videoWidth, video.videoHeight))) return; }
        catch { stopCamera(); notify('No se pudo leer la cámara. Puedes pegar el código.', true); return; }
        timer = setTimeout(tick, 250);
      }
      tick();
    } catch { stopCamera(); if (alive()) throw new TransferError('CAMERA_UNAVAILABLE'); }
  });
  $('stop').onclick = stopCamera;
  return () => { stopped = true; stopCamera(); $('image').onchange = null; $('camera').onclick = null; $('stop').onclick = null; };
}
