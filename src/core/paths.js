import path from 'node:path';

const MAX_NAME_LENGTH = 200;
const FALLBACK_NAME = 'download';

export function safeFileName(name, fallback = FALLBACK_NAME) {
  const cleaned = String(name ?? '')
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/^\.+/, '')
    .trim()
    .slice(0, MAX_NAME_LENGTH);
  return cleaned || fallback;
}

export function safeRelativeDir(dir) {
  if (!dir) return '';
  return String(dir)
    .split(/[\\/]+/)
    .filter((segment) => segment && segment !== '.' && segment !== '..')
    .map((segment) => safeFileName(segment, '_'))
    .join(path.sep);
}
