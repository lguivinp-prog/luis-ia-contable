# ADR-0001: Selección del stack tecnológico

## Estado
Aceptado

## Contexto
El encargo original especifica preferencias claras de stack (Next.js/TypeScript en frontend; Python/FastAPI/SQLAlchemy/Alembic en backend; PostgreSQL; Docker Compose + PowerShell para Windows), salvo incompatibilidad técnica demostrable.

## Decisión
Se adopta el stack tal como se especificó: Next.js 15 (App Router) + TypeScript estricto + React 19 en frontend; FastAPI + Pydantic 2 + SQLAlchemy 2.0 + Alembic en backend; PostgreSQL 16; Docker Compose para el entorno reproducible; scripts PowerShell para instalación/operación en Windows 11.

## Consecuencias
- No se fijan versiones antiguas; se usan versiones estables vigentes al momento de construir el proyecto (ver [ARCHITECTURE.md](../ARCHITECTURE.md) para las versiones exactas registradas).
- El frontend corre dentro de un contenedor Docker con Node, ya que el host de desarrollo (Windows) no tiene Node.js instalado — evita depender de instalar Node localmente.
