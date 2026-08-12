"""Jerarquia criptografica de 2 capas de la boveda SOL (Fase 5, Bloque 5.1,
ver ADR-0068/ADR-0069). Validado experimentalmente en el Spike C; esta
suite cubre la implementacion productiva."""

from __future__ import annotations

import uuid

import pytest

from app.core import sol_vault_crypto as crypto

_FAST_PARAMS = crypto.Argon2Params(time_cost=1, memory_cost_kib=8 * 1024, parallelism=1)


def _ids():
    return {
        "slot_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "vault_id": uuid.uuid4(),
        "client_id": uuid.uuid4(),
        "credential_id": uuid.uuid4(),
        "data_key_row_id": uuid.uuid4(),
    }


def test_production_argon2_params_match_documented_values():
    params = crypto.production_argon2_params()
    assert params.memory_cost_kib == 393216
    assert params.time_cost == 2
    assert params.parallelism == 1
    assert params.hash_len == 32


def test_root_key_wrap_unwrap_round_trip():
    ids = _ids()
    root_key = crypto.generate_key()
    salt = crypto.generate_salt()
    kek = crypto.derive_kek("contrasena-ficticia-de-boveda", salt, _FAST_PARAMS)
    aad = crypto.aad_wrapped_vault_root_key(
        slot_id=ids["slot_id"], tenant_id=ids["tenant_id"], vault_id=ids["vault_id"],
        user_id_or_recovery=str(uuid.uuid4()), kdf_version=1,
    )
    wrapped = crypto.wrap_vault_root_key(root_key, kek, aad)
    assert wrapped != root_key
    assert crypto.unwrap_vault_root_key(wrapped, kek, aad) == root_key


def test_data_key_wrap_unwrap_round_trip():
    ids = _ids()
    root_key = crypto.generate_key()
    data_key = crypto.generate_key()
    aad = crypto.aad_wrapped_data_key(
        data_key_row_id=ids["data_key_row_id"], tenant_id=ids["tenant_id"], vault_id=ids["vault_id"],
        data_key_version=1,
    )
    wrapped = crypto.wrap_data_key(data_key, root_key, aad)
    assert crypto.unwrap_data_key(wrapped, root_key, aad) == data_key


def test_credential_field_encrypt_decrypt_round_trip():
    ids = _ids()
    data_key = crypto.generate_key()
    aad = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    envelope = crypto.encrypt_credential_field("usuario-sol-ficticio", data_key, aad)
    assert crypto.decrypt_credential_field(envelope, data_key, aad) == "usuario-sol-ficticio"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda ids: {**ids, "tenant_id": uuid.uuid4()},
        lambda ids: {**ids, "client_id": uuid.uuid4()},
        lambda ids: {**ids, "credential_id": uuid.uuid4()},
    ],
)
def test_credential_field_aad_tampering_each_id_fails_closed(mutate):
    ids = _ids()
    data_key = crypto.generate_key()
    original_aad = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    envelope = crypto.encrypt_credential_field("usuario-sol-ficticio", data_key, original_aad)

    tampered = mutate(ids)
    tampered_aad = crypto.aad_credential_field(
        credential_id=tampered["credential_id"], tenant_id=tampered["tenant_id"],
        client_id=tampered["client_id"], field_name="sol_username", data_key_version=1,
    )
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.decrypt_credential_field(envelope, data_key, tampered_aad)


def test_credential_field_aad_tampering_field_name_fails_closed():
    ids = _ids()
    data_key = crypto.generate_key()
    aad_username = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    aad_password = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_password", data_key_version=1,
    )
    envelope = crypto.encrypt_credential_field("usuario-sol-ficticio", data_key, aad_username)
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.decrypt_credential_field(envelope, data_key, aad_password)


def test_credential_field_aad_tampering_data_key_version_fails_closed():
    ids = _ids()
    data_key = crypto.generate_key()
    aad_v1 = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    aad_v2 = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=2,
    )
    envelope = crypto.encrypt_credential_field("usuario-sol-ficticio", data_key, aad_v1)
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.decrypt_credential_field(envelope, data_key, aad_v2)


def test_root_key_aad_tampering_user_id_or_recovery_fails_closed():
    ids = _ids()
    salt = crypto.generate_salt()
    kek = crypto.derive_kek("contrasena-ficticia", salt, _FAST_PARAMS)
    root_key = crypto.generate_key()
    aad = crypto.aad_wrapped_vault_root_key(
        slot_id=ids["slot_id"], tenant_id=ids["tenant_id"], vault_id=ids["vault_id"],
        user_id_or_recovery=str(uuid.uuid4()), kdf_version=1,
    )
    wrapped = crypto.wrap_vault_root_key(root_key, kek, aad)
    other_aad = crypto.aad_wrapped_vault_root_key(
        slot_id=ids["slot_id"], tenant_id=ids["tenant_id"], vault_id=ids["vault_id"],
        user_id_or_recovery="RECOVERY", kdf_version=1,
    )
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.unwrap_vault_root_key(wrapped, kek, other_aad)


def test_root_key_aad_tampering_slot_id_fails_closed():
    ids = _ids()
    salt = crypto.generate_salt()
    kek = crypto.derive_kek("contrasena-ficticia", salt, _FAST_PARAMS)
    root_key = crypto.generate_key()
    aad = crypto.aad_wrapped_vault_root_key(
        slot_id=ids["slot_id"], tenant_id=ids["tenant_id"], vault_id=ids["vault_id"],
        user_id_or_recovery=str(uuid.uuid4()), kdf_version=1,
    )
    wrapped = crypto.wrap_vault_root_key(root_key, kek, aad)
    other_aad = crypto.aad_wrapped_vault_root_key(
        slot_id=uuid.uuid4(), tenant_id=ids["tenant_id"], vault_id=ids["vault_id"],
        user_id_or_recovery=str(uuid.uuid4()), kdf_version=1,
    )
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.unwrap_vault_root_key(wrapped, kek, other_aad)


def test_data_key_aad_field_order_distinguishes_tenant_and_vault():
    a, b = uuid.uuid4(), uuid.uuid4()
    row_id = uuid.uuid4()
    aad_ab = crypto.aad_wrapped_data_key(data_key_row_id=row_id, tenant_id=a, vault_id=b, data_key_version=1)
    aad_ba = crypto.aad_wrapped_data_key(data_key_row_id=row_id, tenant_id=b, vault_id=a, data_key_version=1)
    assert aad_ab != aad_ba

    root_key = crypto.generate_key()
    data_key = crypto.generate_key()
    wrapped = crypto.wrap_data_key(data_key, root_key, aad_ab)
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.unwrap_data_key(wrapped, root_key, aad_ba)


def test_ciphertext_moved_between_credential_rows_fails_closed():
    ids = _ids()
    data_key = crypto.generate_key()
    aad_a = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    envelope = crypto.encrypt_credential_field("usuario-de-la-fila-a", data_key, aad_a)
    aad_b = crypto.aad_credential_field(
        credential_id=uuid.uuid4(), tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.decrypt_credential_field(envelope, data_key, aad_b)


def test_wrong_key_fails_closed_even_with_correct_aad():
    ids = _ids()
    aad = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    envelope = crypto.encrypt_credential_field("usuario-sol-ficticio", crypto.generate_key(), aad)
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.decrypt_credential_field(envelope, crypto.generate_key(), aad)


def test_unsupported_format_version_fails_closed():
    ids = _ids()
    data_key = crypto.generate_key()
    aad = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    envelope = bytearray(crypto.encrypt_credential_field("usuario-sol-ficticio", data_key, aad))
    envelope[0] = 99
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.decrypt_credential_field(bytes(envelope), data_key, aad)


def test_truncated_envelope_fails_closed():
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.decrypt_credential_field(b"\x01\x02\x03", crypto.generate_key(), b"aad")


def test_nonce_never_repeats_across_many_calls():
    ids = _ids()
    data_key = crypto.generate_key()
    aad = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    nonces = set()
    for _ in range(300):
        envelope = crypto.encrypt_credential_field("usuario-sol-ficticio", data_key, aad)
        nonce = envelope[1:13]
        assert nonce not in nonces
        nonces.add(nonce)


def test_flipping_a_ciphertext_bit_fails_closed():
    ids = _ids()
    data_key = crypto.generate_key()
    aad = crypto.aad_credential_field(
        credential_id=ids["credential_id"], tenant_id=ids["tenant_id"], client_id=ids["client_id"],
        field_name="sol_username", data_key_version=1,
    )
    envelope = bytearray(crypto.encrypt_credential_field("usuario-sol-ficticio", data_key, aad))
    envelope[-1] ^= 0x01
    with pytest.raises(crypto.SolVaultDecryptionError):
        crypto.decrypt_credential_field(bytes(envelope), data_key, aad)


def test_zeroize_overwrites_buffer_in_place():
    buffer = bytearray(b"clave-secreta-ficticia-de-32-bytes!")
    original_len = len(buffer)
    crypto.zeroize(buffer)
    assert len(buffer) == original_len
    assert all(b == 0 for b in buffer)
