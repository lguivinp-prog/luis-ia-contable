-- Base de datos separada para pruebas automatizadas (pytest), nunca para datos reales.
CREATE DATABASE luis_ia_contable_test;
\c luis_ia_contable_test
CREATE EXTENSION IF NOT EXISTS pgcrypto;
