from src.services.crypto_service import CryptoService


def test_crypto_round_trip():
    crypto = CryptoService("test-key")
    plain = "Bearer abc.def.ghi"

    encrypted = crypto.encrypt(plain)
    assert encrypted and encrypted != plain

    decrypted = crypto.decrypt(encrypted)
    assert decrypted == plain


def test_crypto_none_passthrough():
    crypto = CryptoService("test-key")
    assert crypto.encrypt(None) is None
    assert crypto.decrypt(None) is None
