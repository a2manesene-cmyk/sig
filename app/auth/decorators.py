from functools import wraps

from flask import abort
from flask_login import current_user


def role_required(*codes):
    """Autorise uniquement les rôles dont le code figure dans `codes`."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if not current_user.a_role(*codes):
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def niveau_minimum(niveau):
    """Autorise tout utilisateur dont le niveau hiérarchique est >= `niveau`."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.niveau < niveau:
                abort(403)
            return f(*args, **kwargs)
        return wrapper
    return decorator
