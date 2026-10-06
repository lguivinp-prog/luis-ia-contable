"""Fase 5, Bloque 5.2, Domain E2 -- Agente Windows nativo, endpoint del
IPC agente<->broker ya implementado en Domain D/E1-B/E1-B.1
(`app/broker/`). Ver `docs/phase5/block5_2/BLOCK5_2_WINDOWS_AGENT.md`
para la arquitectura completa.

Alcance explicito de este paquete: identidad local, pairing, mTLS
operacional (HELLO/heartbeat/descubrimiento/reclamo de job/capacidad de
solicitud de credencial), y el ciclo de vida del proceso. NUNCA
automatizacion de navegador -- ver Domain E3 (fuera de alcance aqui)."""

from __future__ import annotations
