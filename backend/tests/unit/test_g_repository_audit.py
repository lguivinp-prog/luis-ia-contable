"""Domain G: auditoria ESTATICA del empaquetado, del despliegue y del repositorio. Lee archivos de la raiz del repo
(`packaging/`, `deploy/`, `docker-compose.yml`, `.gitignore`); en el contenedor Docker del backend esa raiz no esta
montada, por lo que estas pruebas se saltan alli y se ejecutan en el host (venv de backend)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PACKAGING = REPO / "packaging" / "windows-agent"
DEPLOY = REPO / "deploy" / "windows-local"

pytestmark = pytest.mark.skipif(not PACKAGING.is_dir(), reason="raiz del repositorio no disponible (Docker)")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_agent_is_not_a_windows_service_and_uses_per_user_interactive_task():
    manage = _text(PACKAGING / "manage.ps1")
    for forbidden in ("New-Service", "sc.exe create", "sc create", "LocalSystem", "NT AUTHORITY\\SYSTEM", "RunLevel Highest"):
        assert forbidden not in manage, forbidden
    assert "Register-ScheduledTask" in manage
    assert "New-ScheduledTaskTrigger -AtLogOn" in manage
    assert "-LogonType Interactive" in manage and "-RunLevel Limited" in manage
    assert "-MultipleInstances IgnoreNew" in manage  # nunca dos instancias concurrentes
    assert "WINDOWS_SERVICE_REGISTERED" in manage


def test_packaging_is_pyinstaller_onedir_with_pinned_dev_only_dependencies():
    spec = _text(PACKAGING / "luis_sol_agent.spec")
    assert "COLLECT(" in spec and "exclude_binaries=True" in spec  # one-folder, nunca one-file
    reqs = [l for l in _text(PACKAGING / "requirements-packaging.txt").splitlines() if l and not l.startswith("#")]
    assert reqs and all("==" in l for l in reqs)  # todo fijado
    backend_reqs = _text(REPO / "backend" / "requirements.txt")
    assert "pyinstaller" not in backend_reqs.lower()  # empaquetado = solo desarrollo, no dependencia del backend


def test_build_script_produces_manifest_metadata_and_never_signs_silently():
    build = _text(PACKAGING / "build.ps1")
    for needle in ("MANIFEST.sha256", "build_info.json", "playwright_version", "chromium_revision", "pyinstaller_version",
                   "python_version", "architecture", "signed = $false"):
        assert needle in build, needle
    assert "WINDOWS_BINARY_SIGNED=NO" in build  # nunca se afirma una firma inexistente
    executable = re.sub(r"<#.*?#>", "", build, flags=re.S)  # sin el bloque de ayuda
    for forbidden in (".venv-win-e2", "pytest", "live_b", "storageState"):
        assert forbidden not in executable.replace("requirements-packaging", "")


def test_identity_is_never_deleted_by_install_upgrade_or_uninstall():
    manage = _text(PACKAGING / "manage.ps1")
    # solo `purge` borra el directorio de datos (identidad), y exige la frase explicita
    assert len([l for l in manage.splitlines() if "Remove-Item" in l and "$DataDir" in l]) == 1
    purge_start = manage.index('"purge" {')
    assert manage.index("Remove-Item -LiteralPath $DataDir") > purge_start
    assert "$PurgePhrase" in manage[purge_start:] and "DELETE-AGENT-IDENTITY" in manage
    assert "-ConfirmPurge" in manage
    # install/upgrade/uninstall (todo lo anterior a purge) nunca eliminan $DataDir
    assert "Remove-Item" not in "".join(l for l in manage[:purge_start].splitlines(True) if "$DataDir" in l)


def test_ca_certificate_install_refuses_private_keys():
    manage = _text(PACKAGING / "manage.ps1")
    assert "PRIVATE KEY" in manage and "BEGIN CERTIFICATE" in manage


def test_development_compose_does_not_enable_the_broker():
    compose = _text(REPO / "docker-compose.yml")
    assert not re.search(r"SOL_BROKER_ENABLED\s*[:=]\s*[\"']?true", compose, re.IGNORECASE)
    env_example = _text(REPO / ".env.example")
    assert not re.search(r"^SOL_BROKER_ENABLED\s*=\s*true", env_example, re.MULTILINE | re.IGNORECASE)


def test_product_deployment_profile_enables_the_broker_on_loopback_only():
    example = _text(DEPLOY / "sol-local.env.example")
    assert re.search(r"^SOL_BROKER_ENABLED=true$", example, re.MULTILINE)
    assert re.search(r"^SOL_BROKER_HOST=127\.0\.0\.1$", example, re.MULTILINE)
    assert re.search(r"^BACKEND_WORKERS=1$", example, re.MULTILINE)
    start = _text(DEPLOY / "start-backend.ps1")
    assert '"--host", "127.0.0.1"' in start and '"--workers", "1"' in start
    assert "BROKER_CA_MATERIAL_MISSING" in start  # falla cerrado, nunca autogenera
    assert "0.0.0.0" not in start and "0.0.0.0" not in example


def test_ca_provisioning_script_refuses_ca_inside_the_repository():
    script = _text(DEPLOY / "provision-broker-ca.ps1")
    assert "FUERA del repositorio" in script


def test_no_secret_material_or_runtime_artifacts_are_tracked_in_packaging_or_deploy():
    forbidden_names = re.compile(r"\.(pem|key|pfx|p12|dpapi|db|sqlite|log|flag)$|sol-local\.env$|live_b|storageState", re.I)
    for root in (PACKAGING, DEPLOY):
        for path in root.rglob("*"):
            rel = path.relative_to(REPO).as_posix()
            if path.is_file() and not rel.startswith("packaging/windows-agent/dist/") and not rel.startswith(
                "packaging/windows-agent/build/"
            ):
                assert not forbidden_names.search(path.name), rel
                if path.suffix in {".ps1", ".txt", ".spec", ".py", ".example"} or path.name == "VERSION":
                    assert "BEGIN PRIVATE KEY" not in _text(path) and "BEGIN RSA PRIVATE KEY" not in _text(path), rel


def test_build_outputs_are_git_ignored():
    gitignore = _text(REPO / ".gitignore")
    for entry in ("packaging/windows-agent/dist/", "packaging/windows-agent/build/", "deploy/windows-local/sol-local.env"):
        assert entry in gitignore


# --- auditoria de alcance del producto (Block 5.2): solo AUTHENTICATE_TO_LANDING_ONLY -----------------------------


_PRODUCT_CODE = [
    *(REPO / "backend" / "windows_agent").glob("*.py"),
    *(REPO / "backend" / "app" / "broker").glob("*.py"),
    REPO / "backend" / "app" / "routers" / "sol_access.py",
    *(REPO / "frontend" / "src" / "lib").glob("solAccess*.ts"),
    REPO / "frontend" / "src" / "app" / "(app)" / "clients" / "[id]" / "sol-access" / "page.tsx",
]


def _code_only(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'"""[\s\S]*?"""', "", text)  # docstrings (los terminos prohibidos aparecen en negativo)
    text = re.sub(r"/\*[\s\S]*?\*/", "", text)  # bloques /* */
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith(("#", "//", "*"))
    )


@pytest.mark.parametrize("path", [p for p in _PRODUCT_CODE if p.name != "_manual_windows_validation.py"], ids=lambda p: p.name)
def test_product_code_has_no_post_landing_automation_or_persistent_browser_state(path):
    code = _code_only(path)
    for forbidden in (
        r"storage_state\(", r"storageState", r"launch_persistent_context", r"user_data_dir", r"record_video",
        r"tracing\.start", r"\.screenshot\(", r"page\.pdf\(", r"expect_download", r"set_input_files",
        r"\.key-bridge-",
    ):
        assert not re.search(forbidden, code), (path.name, forbidden)
    for forbidden_feature in (r"buz[oó]n\w*\s*\(", r"def\s+\w*(read|list|download)_?(message|mailbox|attachment)",
                              r"def\s+\w*(file|submit)_?declaration", r"def\s+\w*(rectif|pay)\w*\("):
        assert not re.search(forbidden_feature, code, re.IGNORECASE), (path.name, forbidden_feature)


# --- R1: el instalador auxiliar de Chromium (setup.exe) no viaja en el paquete (Defender lo elimina al extraer el ZIP) ---


def test_build_excludes_only_chromium_setup_exe_and_requires_chrome_exe():
    build = _text(PACKAGING / "build.ps1")
    assert r"chrome-win64\setup.exe" in build and "/XF $setupExe" in build
    assert r"chrome-win64\chrome.exe" in build and "Falta el ejecutable requerido del navegador" in build
    assert "*.exe" not in build  # nunca una exclusion amplia de ejecutables
    assert "1243" not in build  # regla independiente de la revision


def test_built_package_has_chrome_exe_but_no_setup_exe_in_tree_or_manifest():
    packages = [p for p in (PACKAGING / "dist").glob("luis-sol-agent-*-win-x64") if p.is_dir()]
    if not packages:
        pytest.skip("no hay paquete construido en dist/")
    pkg = packages[0]
    chromium_dirs = list((pkg / "browsers").glob("chromium-*"))
    assert chromium_dirs, "falta el Chromium empaquetado"
    for chromium in chromium_dirs:
        assert (chromium / "chrome-win64" / "chrome.exe").is_file()
        assert not (chromium / "chrome-win64" / "setup.exe").exists()
    manifest = (pkg / "MANIFEST.sha256").read_text(encoding="utf-8")
    assert "chrome-win64/chrome.exe" in manifest and "setup.exe" not in manifest
