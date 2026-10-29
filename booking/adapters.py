import base64
import hashlib

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from allauth.mfa.adapter import DefaultMFAAdapter


class EncryptedMFAAdapter(DefaultMFAAdapter):
    def _cipher(self):
        keys = [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
        return MultiFernet([Fernet(base64.urlsafe_b64encode(hashlib.sha256((key + ":studio-mfa").encode()).digest())) for key in keys])

    def encrypt(self, text):
        return self._cipher().encrypt(text.encode()).decode()

    def decrypt(self, encrypted_text):
        return self._cipher().decrypt(encrypted_text.encode()).decode()

    def can_delete_authenticator(self, authenticator):
        return not (authenticator.user.is_staff and authenticator.type == "totp")
