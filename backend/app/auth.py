"""Admin login. Users never log in; only the admin pages need the password.

Set the password with the ADMIN_PASSWORD environment variable before deploying.
"""
import hmac, os, secrets, time

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
SESSION_HOURS = 12
COOKIE = 'admin_token'
_sessions = {}                                  # token -> expiry time
_bearer = HTTPBearer(auto_error=False)

if ADMIN_PASSWORD == 'admin123':
    print('WARNING: using the default admin password "admin123". Set ADMIN_PASSWORD before deploying.')


def login(password):
    if not hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        return None
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_HOURS * 3600
    return token


def logout(token):
    _sessions.pop(token, None)


def valid(token):
    exp = _sessions.get(token or '')
    if exp and exp > time.time():
        return True
    _sessions.pop(token or '', None)
    return False


def token_from(request: Request, cred: HTTPAuthorizationCredentials | None = None):
    if cred and cred.credentials:
        return cred.credentials
    return request.cookies.get(COOKIE) or request.query_params.get('token')


def require_admin(request: Request, cred: HTTPAuthorizationCredentials = Depends(_bearer)):
    token = token_from(request, cred)
    if not valid(token):
        raise HTTPException(401, 'Admin login required')
    return token
