"""
Auth Facade
-----------
Bridge for authentication services in the UI.
"""
from core.auth.auth_service import AuthService

class AuthFacade:
    def __init__(self):
        self.service = AuthService()

    def login(self, username, password):
        return self.service.authenticate(username, password)
