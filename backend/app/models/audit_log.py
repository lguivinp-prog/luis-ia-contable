import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class AuditAction(str, enum.Enum):
    # Nota (Bloque 2 / ADR-0009): ya no existe LOGIN_FAILURE en audit_logs.
    # Los fallos de login (exista o no la cuenta) se registran exclusivamente
    # en security_events + login_attempts. audit_logs queda reservado para
    # actuaciones dentro de una sesion autenticada.
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGOUT = "LOGOUT"
    PASSWORD_CHANGED = "PASSWORD_CHANGED"
    SESSION_REVOKED = "SESSION_REVOKED"
    SESSION_REVOKED_ALL_OTHERS = "SESSION_REVOKED_ALL_OTHERS"
    CLIENT_CREATE = "CLIENT_CREATE"
    CLIENT_UPDATE = "CLIENT_UPDATE"
    CLIENT_STATUS_CHANGE = "CLIENT_STATUS_CHANGE"
    # Correccion MANUAL del regimen tributario por una persona (nunca atribuida a una extraccion de SUNAT)
    CLIENT_TAX_REGIME_MANUAL_CHANGE = "CLIENT_TAX_REGIME_MANUAL_CHANGE"
    CLIENT_OBLIGATION_ASSIGN = "CLIENT_OBLIGATION_ASSIGN"
    CLIENT_OBLIGATION_UPDATE = "CLIENT_OBLIGATION_UPDATE"
    CLIENT_OBLIGATION_REMOVE = "CLIENT_OBLIGATION_REMOVE"
    RUC_LOOKUP_QUERY = "RUC_LOOKUP_QUERY"
    TAX_CATEGORY_ASSIGN = "TAX_CATEGORY_ASSIGN"
    TAX_CATEGORY_REMOVE = "TAX_CATEGORY_REMOVE"
    TAX_SCHEDULE_SOURCE_CREATE = "TAX_SCHEDULE_SOURCE_CREATE"
    TAX_SCHEDULE_IMPORT_RUN_CREATE = "TAX_SCHEDULE_IMPORT_RUN_CREATE"
    TAX_SCHEDULE_IMPORT_CONFIRM = "TAX_SCHEDULE_IMPORT_CONFIRM"
    TAX_SCHEDULE_IMPORT_REJECT = "TAX_SCHEDULE_IMPORT_REJECT"
    TAX_SCHEDULE_SUBMIT_FOR_REVIEW = "TAX_SCHEDULE_SUBMIT_FOR_REVIEW"
    TAX_SCHEDULE_APPROVE = "TAX_SCHEDULE_APPROVE"
    TAX_SCHEDULE_REJECT = "TAX_SCHEDULE_REJECT"
    TAX_SCHEDULE_CANCEL = "TAX_SCHEDULE_CANCEL"
    TAX_SCHEDULE_PREVIEW_QUERY = "TAX_SCHEDULE_PREVIEW_QUERY"
    TAX_PERIOD_CREATE = "TAX_PERIOD_CREATE"
    TAX_PERIOD_CLOSE = "TAX_PERIOD_CLOSE"
    TAX_PERIOD_REOPEN = "TAX_PERIOD_REOPEN"
    DECLARATION_CREATE = "DECLARATION_CREATE"
    DECLARATION_WORKFLOW_CHANGE = "DECLARATION_WORKFLOW_CHANGE"
    DECLARATION_ASSIGN = "DECLARATION_ASSIGN"
    DECLARATION_FILE = "DECLARATION_FILE"
    DECLARATION_FILING_CORRECT = "DECLARATION_FILING_CORRECT"
    DECLARATION_RECALCULATE = "DECLARATION_RECALCULATE"
    DECLARATION_REOPEN = "DECLARATION_REOPEN"
    DECLARATION_CLOSE = "DECLARATION_CLOSE"
    DECLARATION_CANCEL = "DECLARATION_CANCEL"
    DECLARATION_GENERATION_PREVIEW = "DECLARATION_GENERATION_PREVIEW"
    DECLARATION_GENERATION_ITEM_EXCLUDE = "DECLARATION_GENERATION_ITEM_EXCLUDE"
    DECLARATION_GENERATION_CONFIRM = "DECLARATION_GENERATION_CONFIRM"
    DECLARATION_GENERATION_REFRESH_PREVIEW = "DECLARATION_GENERATION_REFRESH_PREVIEW"
    DECLARATION_GENERATION_CANCEL = "DECLARATION_GENERATION_CANCEL"
    # ---- Bloque 3.2: control documental ----
    DOCUMENT_CATALOG_CREATE = "DOCUMENT_CATALOG_CREATE"
    DOCUMENT_REQUIREMENT_ASSIGN = "DOCUMENT_REQUIREMENT_ASSIGN"
    DOCUMENT_ITEM_STATUS_CHANGE = "DOCUMENT_ITEM_STATUS_CHANGE"
    DOCUMENT_REQUIREMENTS_SYNC = "DOCUMENT_REQUIREMENTS_SYNC"
    # ---- Bloque 3.2: determinacion y pagos tributarios ----
    TAX_ASSESSMENT_CREATE = "TAX_ASSESSMENT_CREATE"
    TAX_ASSESSMENT_CORRECT = "TAX_ASSESSMENT_CORRECT"
    TAX_PAYMENT_CREATE = "TAX_PAYMENT_CREATE"
    TAX_PAYMENT_VOID = "TAX_PAYMENT_VOID"
    TAX_PAYMENT_OVERPAYMENT_CONFIRM = "TAX_PAYMENT_OVERPAYMENT_CONFIRM"
    # ---- Bloque 3.2: honorarios profesionales ----
    FEE_AGREEMENT_CREATE = "FEE_AGREEMENT_CREATE"
    FEE_AGREEMENT_SUPERSEDE = "FEE_AGREEMENT_SUPERSEDE"
    FEE_AGREEMENT_CANCEL = "FEE_AGREEMENT_CANCEL"
    FEE_CHARGE_CREATE = "FEE_CHARGE_CREATE"
    FEE_PAYMENT_CREATE = "FEE_PAYMENT_CREATE"
    FEE_PAYMENT_VOID = "FEE_PAYMENT_VOID"
    FEE_RECEIPT_ISSUE = "FEE_RECEIPT_ISSUE"
    FEE_RECEIPT_VOID = "FEE_RECEIPT_VOID"
    # ---- Bloque 3.2: cierre completo y reportes ----
    DECLARATION_CLOSE_WITH_OVERRIDE = "DECLARATION_CLOSE_WITH_OVERRIDE"
    REPORT_EXPORT = "REPORT_EXPORT"
    # ---- Bloque 4: RBAC real y gestion de usuarios ----
    USER_CREATE = "USER_CREATE"
    USER_ROLE_CHANGE = "USER_ROLE_CHANGE"
    USER_DEACTIVATE = "USER_DEACTIVATE"
    USER_REACTIVATE = "USER_REACTIVATE"
    USER_PASSWORD_RESET = "USER_PASSWORD_RESET"
    # ---- Bloque 5: almacenamiento real de archivos documentales ----
    DOCUMENT_ITEM_FILE_UPLOAD = "DOCUMENT_ITEM_FILE_UPLOAD"
    DOCUMENT_ITEM_FILE_DOWNLOAD = "DOCUMENT_ITEM_FILE_DOWNLOAD"
    DOCUMENT_ITEM_FILE_REPLACE = "DOCUMENT_ITEM_FILE_REPLACE"
    DOCUMENT_ITEM_FILE_DELETE = "DOCUMENT_ITEM_FILE_DELETE"
    # ---- Fase 2, Bloque 1: gestion operativa de tareas ----
    TASK_CREATE = "TASK_CREATE"
    TASK_UPDATE = "TASK_UPDATE"
    TASK_ASSIGN = "TASK_ASSIGN"
    TASK_START = "TASK_START"
    TASK_COMPLETE = "TASK_COMPLETE"
    TASK_CANCEL = "TASK_CANCEL"
    TASK_RESCHEDULE = "TASK_RESCHEDULE"
    TASK_COMMENT = "TASK_COMMENT"
    TASK_GENERATE = "TASK_GENERATE"
    TASK_SOURCE_UPDATE = "TASK_SOURCE_UPDATE"
    # ---- Fase 3, Bloque 3.1: integracion Google OAuth (identidad) ----
    INTEGRATION_GOOGLE_CONNECT = "INTEGRATION_GOOGLE_CONNECT"
    INTEGRATION_GOOGLE_CONNECT_FAILED = "INTEGRATION_GOOGLE_CONNECT_FAILED"
    INTEGRATION_GOOGLE_DISCONNECT = "INTEGRATION_GOOGLE_DISCONNECT"
    INTEGRATION_GOOGLE_ACCOUNT_REPLACED = "INTEGRATION_GOOGLE_ACCOUNT_REPLACED"
    # ---- Fase 3, Bloque 3.2: comunicaciones asistidas por WhatsApp ----
    COMMUNICATION_DRAFT_CREATE = "COMMUNICATION_DRAFT_CREATE"
    COMMUNICATION_OPEN_REQUEST = "COMMUNICATION_OPEN_REQUEST"
    COMMUNICATION_MARK_SENT = "COMMUNICATION_MARK_SENT"
    COMMUNICATION_DISCARD = "COMMUNICATION_DISCARD"
    # ---- Fase 5, Bloque 5.1: boveda de credenciales SOL (ver ADR-0068) ----
    SOL_VAULT_INITIALIZE = "SOL_VAULT_INITIALIZE"
    SOL_VAULT_RECOVERY_CONFIRM = "SOL_VAULT_RECOVERY_CONFIRM"
    SOL_VAULT_RECOVERY_REPLACE = "SOL_VAULT_RECOVERY_REPLACE"
    SOL_VAULT_RECOVER = "SOL_VAULT_RECOVER"
    SOL_VAULT_UNLOCK_SUCCESS = "SOL_VAULT_UNLOCK_SUCCESS"
    SOL_VAULT_UNLOCK_FAILED = "SOL_VAULT_UNLOCK_FAILED"
    SOL_VAULT_LOCK = "SOL_VAULT_LOCK"
    SOL_VAULT_PASSWORD_CHANGE = "SOL_VAULT_PASSWORD_CHANGE"
    SOL_VAULT_SLOT_INVITE = "SOL_VAULT_SLOT_INVITE"
    SOL_VAULT_SLOT_INVITATION_REAUTHORIZE = "SOL_VAULT_SLOT_INVITATION_REAUTHORIZE"
    SOL_VAULT_SLOT_INVITATION_CANCEL = "SOL_VAULT_SLOT_INVITATION_CANCEL"
    SOL_VAULT_SLOT_ADD = "SOL_VAULT_SLOT_ADD"
    SOL_VAULT_SLOT_REVOKE = "SOL_VAULT_SLOT_REVOKE"
    SOL_VAULT_DATA_KEY_ROTATE = "SOL_VAULT_DATA_KEY_ROTATE"
    SOL_CREDENTIAL_CREATE = "SOL_CREDENTIAL_CREATE"
    SOL_CREDENTIAL_UPDATE = "SOL_CREDENTIAL_UPDATE"
    SOL_CREDENTIAL_DISABLE = "SOL_CREDENTIAL_DISABLE"
    # ---- Fase 5, Bloque 5.2: acceso SOL supervisado (ver docs/phase5/block5_2/,
    # BLOCK5_2_SECURITY_MODEL.md seccion 7) -- solo el enum, sin emision de
    # eventos todavia (Domain B es fundacion de persistencia, no servicios) ----
    SOL_SESSION_CREATED = "SOL_SESSION_CREATED"
    AGENT_ASSIGNED = "AGENT_ASSIGNED"
    JOB_CLAIMED = "JOB_CLAIMED"
    AUTH_STARTED = "AUTH_STARTED"
    WAITING_FOR_HUMAN = "WAITING_FOR_HUMAN"
    AUTH_SUCCEEDED = "AUTH_SUCCEEDED"
    # M2: navegacion supervisada, Ficha RUC y alta asistida (metadata sin credenciales/HTML/cookies)
    SUNAT_DESTINATION_REQUESTED = "SUNAT_DESTINATION_REQUESTED"
    # M3: aceptacion real controlada de un destino (autorizacion acotada, consumo, revocacion, resultado confirmado por el propietario)
    # M4: control SIRE (metadata sin importes ni contenido de archivos)
    SIRE_CONTROL_CREATED = "SIRE_CONTROL_CREATED"
    SIRE_SOURCE_ADDED = "SIRE_SOURCE_ADDED"
    SIRE_FILE_STORED = "SIRE_FILE_STORED"
    SIRE_REVIEW_RECORDED = "SIRE_REVIEW_RECORDED"
    SIRE_TOLERANCE_SET = "SIRE_TOLERANCE_SET"
    SIRE_FILE_DOWNLOADED = "SIRE_FILE_DOWNLOADED"
    SOL_ACCEPTANCE_GRANTED = "SOL_ACCEPTANCE_GRANTED"
    SOL_ACCEPTANCE_CONSUMED = "SOL_ACCEPTANCE_CONSUMED"
    SOL_ACCEPTANCE_REVOKED = "SOL_ACCEPTANCE_REVOKED"
    SOL_ACCEPTANCE_RESULT_RECORDED = "SOL_ACCEPTANCE_RESULT_RECORDED"
    SOL_HUMAN_CONTROL_STARTED = "SOL_HUMAN_CONTROL_STARTED"
    FICHA_RUC_RETRIEVED = "FICHA_RUC_RETRIEVED"
    FICHA_PROPOSAL_CREATED = "FICHA_PROPOSAL_CREATED"
    FICHA_PROPOSAL_ACCEPTED = "FICHA_PROPOSAL_ACCEPTED"
    FICHA_PROPOSAL_REJECTED = "FICHA_PROPOSAL_REJECTED"
    FICHA_VERIFIED_NO_CHANGES = "FICHA_VERIFIED_NO_CHANGES"
    ONBOARDING_STARTED = "ONBOARDING_STARTED"
    ONBOARDING_COMPLETED = "ONBOARDING_COMPLETED"
    ONBOARDING_CANCELLED = "ONBOARDING_CANCELLED"
    TAXPAYER_CONFIRMED = "TAXPAYER_CONFIRMED"
    SESSION_CANCELLED = "SESSION_CANCELLED"
    SESSION_FAILED = "SESSION_FAILED"
    LOGOUT_OBSERVED = "LOGOUT_OBSERVED"
    SESSION_CLEANUP_COMPLETED = "SESSION_CLEANUP_COMPLETED"
    # ---- Fase 5, Bloque 5.2, Domain C (ver docs/phase5/block5_2/
    # BLOCK5_2_IMPLEMENTATION_PLAN.md informe de Domain C, seccion "audit
    # emission") -- adicion aditiva, sin migracion (esta columna es
    # `String(60)` sin CheckConstraint): los 11 valores de Domain A.2
    # cubrian solo una parte del ciclo de vida real que Domain C implementa.
    # Se añaden EXCLUSIVAMENTE los 4 eventos de mayor valor forense/de
    # seguridad sin ningun valor previo que los cubriera razonablemente
    # (nueva identidad de agente por pairing, revocacion de agente, bloqueo
    # por fuerza bruta de pairing, consumo del gate de entrega de credencial)
    # -- mismo patron ya usado en cada bloque anterior de este enum (cada uno
    # añadio su propio banner al implementar sus servicios reales, no en su
    # ronda de solo-DDL). El resto del ciclo de vida (creacion/cancelacion/
    # expiracion de pairing, creacion/completado/fallo/expiracion de job,
    # expiracion de sesion) queda sin AuditAction dedicado en esta ronda --
    # decision deliberada y documentada en el informe de Domain C, no un
    # olvido: esas filas ya llevan su propio `status`/`terminal_reason_code`/
    # timestamps trazables en su tabla.
    #
    # Ratificados formalmente en Domain C.1 (ver informe de Domain C.1,
    # Gate 4 / docs/phase5/block5_2/BLOCK5_2_SECURITY_MODEL.md seccion 7):
    # los 4 representan una transicion de seguridad/forense real que
    # ningun valor de los 11 anteriores cubria razonablemente. Ninguno se
    # retira.
    PAIRING_COMPLETED = "PAIRING_COMPLETED"
    PAIRING_LOCKED = "PAIRING_LOCKED"
    AGENT_REVOKED = "AGENT_REVOKED"
    # IMPORTANTE (Domain C.1, Gate/seccion 9): este evento significa
    # UNICAMENTE que el gate transaccional de "a lo sumo una entrega de
    # credencial por sesion" fue consumido (credential_delivered_at
    # poblado) -- JAMAS que la credencial en claro efectivamente llego al
    # agente por la red. El transporte real (broker/mTLS/Windows Agent) es
    # de un dominio posterior, fuera de alcance de Domain C, y puede fallar
    # incluso despues de que este evento se emita. Nunca leer este evento
    # como "credential delivered to agent".
    CREDENTIAL_DELIVERY_GATE_CONSUMED = "CREDENTIAL_DELIVERY_GATE_CONSUMED"
    # ---- Fase 5, Bloque 5.2, Domain E1-B (ver docs/phase5/block5_2/
    # BLOCK5_2_SECRET_DELIVERY_ARCHITECTURE.md seccion 20, ratificada en
    # Domain E1-A/E1-A.1) -- adicion aditiva, sin migracion. Semantica
    # EXACTA (corregida en el checkpoint final de Domain E1-A): significa
    # UNICAMENTE que un usuario autorizado aprobo con exito una
    # autorizacion de entrega de credencial de corta vida para la
    # sesion/job SOL vinculados -- prueba que la DECISION DE AUTORIZACION
    # quedo registrada de forma durable. JAMAS prueba que el
    # `SecretDeliveryGrant` process-local llego a materializarse, que la
    # credencial fue descifrada, que el gate de BD fue consumido, que
    # hubo transmision de red, ni que el agente recibio el secreto --
    # ninguna transaccion atomica distribuida existe entre esta fila y el
    # registro process-local (ver el documento de arquitectura, seccion
    # 20.1). Accion DISTINTA e independiente de
    # CREDENTIAL_DELIVERY_GATE_CONSUMED, que sigue siendo la unica
    # autoridad durable sobre el consumo real del gate.
    CREDENTIAL_DELIVERY_AUTHORIZED = "CREDENTIAL_DELIVERY_AUTHORIZED"


class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_logs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    log_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
