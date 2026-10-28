export class HttpError extends Error {
  constructor(status, statusText = '', url = '', retryAfterSeconds = null) {
    super(`HTTP ${status}${statusText ? ` ${statusText}` : ''}${url ? ` (${url})` : ''}`);
    this.name = 'HttpError';
    this.status = status;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export class ProviderError extends Error {
  constructor(message, { provider } = {}) {
    super(provider ? `[${provider}] ${message}` : message);
    this.name = 'ProviderError';
    this.provider = provider;
  }
}

export class CancelledError extends Error {
  constructor(message = 'Download cancelled') {
    super(message);
    this.name = 'CancelledError';
  }
}

export function isRetryable(error) {
  if (error instanceof HttpError) {
    return error.status === 408 || error.status === 429 || error.status >= 500;
  }
  if (error instanceof ProviderError || error instanceof CancelledError) return false;
  return true;
}
