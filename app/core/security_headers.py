"""Response hardening headers for the management console and the API.

Why these headers and not others:

* ``Content-Security-Policy`` restricts where the console may load code from, blocks
  plugin content (``object-src 'none'``), forbids base-tag rewriting
  (``base-uri 'self'``) and prevents the console from being framed
  (``frame-ancestors 'none'``). ``script-src`` still allows ``'unsafe-inline'``
  because the console ships two inline bootstrap scripts (``static/index.html``,
  ``static/login.html``) and the sandboxed artifact preview injects a small inline
  bridge into its ``srcdoc`` document. Moving those to a nonce-based policy is a
  follow-up that must also relocate the preview bridge out of ``srcdoc``; the current
  policy still blocks every third-party script origin except the one CDN the preview
  uses.
* ``X-Content-Type-Options: nosniff`` stops a browser from re-interpreting a
  response as HTML/JS.
* ``X-Frame-Options: DENY`` is the legacy fallback for browsers that ignore CSP.
* ``Referrer-Policy: no-referrer`` keeps API keys and request paths out of the
  ``Referer`` header of outbound links.
* ``Permissions-Policy`` disables the browser features the console never uses.
* ``Cross-Origin-Opener-Policy: same-origin`` isolates the console's browsing context
  group (``window.opener`` is not used by the console).

None of these headers change the HTTP contract of the API: no header is required by
clients, no response body changes and no status code changes.
"""
from __future__ import annotations

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    # 'unsafe-inline' is required by the console's inline bootstrap scripts and by the
    # sandboxed artifact preview bridge (see the module docstring).
    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data: https://cdnjs.cloudflare.com; "
    "connect-src 'self'; "
    "media-src 'self' data: blob:; "
    "frame-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def apply_security_headers(response) -> None:
    """Attach the hardening headers to a response, without overwriting existing ones."""
    headers = getattr(response, "headers", None)
    if headers is None:
        return
    for name, value in SECURITY_HEADERS.items():
        headers.setdefault(name, value)
