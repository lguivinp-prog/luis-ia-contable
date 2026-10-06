"""HARNESS (no es producto ni prueba de la suite): proceso Windows nativo que
usa el codigo PRODUCTIVO del agente (identidad DPAPI real, mTLS real,
`OperationalBrokerClient.fetch_credential_for_job`) para reclamar un job,
recibir la credencial SOL de PRUEBA y MANTENERLA EN MEMORIA, de modo que el
orquestador pueda terminarlo abruptamente (TerminateProcess) mientras el
secreto esta vivo en el proceso. Solo credenciales ficticias de prueba.

Uso: python harness_secret_holder.py <data_dir> <harness_out_dir>"""

from __future__ import annotations

import pathlib
import sys
import time

from windows_agent.config import load_config
from windows_agent.identity import WindowsDpapiIdentityStore
from windows_agent.runtime import AgentRuntime

data_dir, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
config = load_config(data_dir)
store = WindowsDpapiIdentityStore(data_dir)
ca_pem = (out / "ca.pem").read_bytes()
runtime = AgentRuntime(
    identity=store.load(),
    config=config,
    ssl_context_factory=lambda: store.build_client_ssl_context(ca_certificate_pem=ca_pem),
)
runtime.connect()
job_id = None
for _ in range(120):
    job_id = runtime.poll_and_claim_once()
    if job_id is not None:
        break
    runtime.heartbeat_once()
    time.sleep(0.5)
assert job_id is not None, "no se reclamo ningun job"
(out / "holder-claimed").write_text("1")
while not (out / "holder-authorized").exists():
    runtime.heartbeat_once()
    time.sleep(0.3)
with runtime.credential_client().fetch_credential_for_job(
    connection_id=runtime.connection_id, job_id=job_id
) as credential:
    # el secreto esta vivo en memoria de ESTE proceso; solo se escribe una marca
    # sin contenido secreto (longitud) para que el orquestador sepa que ya llego.
    (out / "holder-holding").write_text(str(len(credential.sol_password)))
    time.sleep(120)
