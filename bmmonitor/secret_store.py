"""Salaisuuksien tallennus käyttöjärjestelmän salasanavarastoon.

Windows: Tunnistetietojen hallinta (Credential Manager), macOS: Avainnippu (Keychain).
Jos varastoa ei ole (esim. Linux ilman Secret Serviceä), salaisuus jää asetustiedostoon
kuten ennenkin – kutsuja saa tiedon paluuarvosta.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("bmmonitor.secrets")
SERVICE = "Monitor for BrandMeister"
_backend = None
_checked = False


def _keyring():
    """Valitsee alustan varaston suoraan (toimii myös PyInstaller-paketissa ilman entry pointeja)."""
    global _backend, _checked
    if _checked:
        return _backend
    _checked = True
    try:
        import keyring
        if sys.platform == "win32":
            from keyring.backends import Windows
            keyring.set_keyring(Windows.WinVaultKeyring())
        elif sys.platform == "darwin":
            from keyring.backends import macOS
            keyring.set_keyring(macOS.Keyring())
        else:
            from keyring.backends import fail
            if isinstance(keyring.get_keyring(), fail.Keyring):
                return None
        _backend = keyring
    except Exception as e:  # noqa: BLE001
        log.info("Salasanavarasto ei käytettävissä (%s) – salaisuus tallennetaan asetustiedostoon", e)
        _backend = None
    return _backend


def available() -> bool:
    return _keyring() is not None


def put(name: str, value: str) -> bool:
    """Tallentaa salaisuuden. True = varastoon, False = varasto ei käytettävissä."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password(SERVICE, name, value)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Salaisuuden tallennus varastoon epäonnistui: %s", e)
        return False


def get(name: str) -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(SERVICE, name)
    except Exception as e:  # noqa: BLE001
        log.warning("Salaisuuden luku varastosta epäonnistui: %s", e)
        return None


def delete(name: str) -> None:
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE, name)
    except Exception:  # noqa: BLE001  (ei ollut tallessa)
        pass


def load_field(cfg: dict, field: str, name: str) -> tuple[str, bool]:
    """Lukee salaisen asetuksen. Asetustiedostossa oleva arvo siirretään varastoon, jos se on käytettävissä.
    Palauttaa (arvo, muuttuiko cfg)."""
    if cfg.get(field + "_store") == "keyring":
        return get(name) or "", False
    value = str(cfg.get(field) or "")
    if value and put(name, value):
        cfg[field] = ""
        cfg[field + "_store"] = "keyring"
        return value, True
    return value, False


def save_field(cfg: dict, field: str, name: str, value: str) -> None:
    """Tallentaa salaisen asetuksen varastoon; jos varastoa ei ole, asetustiedostoon."""
    cfg.pop(field + "_store", None)
    cfg[field] = ""
    if not value:
        delete(name)
    elif put(name, value):
        cfg[field + "_store"] = "keyring"
    else:
        cfg[field] = value
