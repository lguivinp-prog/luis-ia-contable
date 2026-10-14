from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.client_ficha_ruc import ClientFichaRucSnapshot, ClientOnboarding
from app.models.client_period_operation import ClientPeriodOperation, ClientPeriodOperationHistory
from app.models.client_sol_credential import ClientSolCredential
from app.models.communication_draft import CommunicationDraft
from app.models.declaration import (
    Declaration,
    DeclarationAssignmentHistory,
    DeclarationDueDateHistory,
    DeclarationReopenHistory,
    DeclarationStatusHistory,
    FilingHistory,
)
from app.models.declaration_document import (
    ClientDocumentRequirement,
    DeclarationDocumentItem,
    DocumentItemStatusHistory,
    DocumentRequirementCatalog,
)
from app.models.declaration_document_file import DeclarationDocumentFile
from app.models.declaration_generation import (
    DeclarationGenerationRun,
    DeclarationGenerationRunItem,
)
from app.models.google_oauth import (
    GoogleOAuthAuthorizationAttempt,
    GoogleOAuthConnection,
)
from app.models.login_attempt import LoginAttempt
from app.models.obligation import ClientObligation, ObligationCatalog
from app.models.operational_task import (
    OperationalTask,
    TaskChangeHistory,
    TaskComment,
    TaskGenerationRun,
)
from app.models.professional_fee import (
    ProfessionalFeeAgreement,
    ProfessionalFeeAgreementHistory,
    ProfessionalFeeCharge,
    ProfessionalFeeChargeStatusHistory,
    ProfessionalFeePayment,
    ProfessionalFeePaymentHistory,
    ProfessionalReceipt,
)
from app.models.security_event import SecurityEvent
from app.models.session import Session
from app.models.sol_destination_acceptance import SolDestinationAcceptance
from app.models.sol_agent_access import (
    SolAccessSession,
    SolAgent,
    SolAgentJob,
    SolAgentPairing,
)
from app.models.sol_vault import (
    SolVault,
    SolVaultDataKey,
    SolVaultInvitation,
    SolVaultKeySlot,
)
from app.models.tax_assessment import TaxAssessment, TaxAssessmentHistory
from app.models.tax_category import ClientTaxCategory, TaxCategoryCatalog
from app.models.tax_payment import TaxPayment, TaxPaymentHistory
from app.models.tax_period import TaxPeriod, TaxPeriodStatusHistory
from app.models.tax_schedule import (
    TaxScheduleApprovalHistory,
    TaxScheduleEntry,
    TaxScheduleException,
    TaxScheduleImportRun,
    TaxScheduleSource,
    TaxScheduleVersion,
)
from app.models.tenant import Tenant
from app.models.user import User

__all__ = [
    "AuditLog",
    "Client",
    "ClientDocumentRequirement",
    "ClientFichaRucSnapshot",
    "ClientOnboarding",
    "ClientPeriodOperation",
    "ClientPeriodOperationHistory",
    "ClientObligation",
    "ClientSolCredential",
    "ClientTaxCategory",
    "CommunicationDraft",
    "Declaration",
    "DeclarationAssignmentHistory",
    "DeclarationDocumentFile",
    "DeclarationDocumentItem",
    "DeclarationDueDateHistory",
    "DeclarationGenerationRun",
    "DeclarationGenerationRunItem",
    "DeclarationReopenHistory",
    "DeclarationStatusHistory",
    "DocumentItemStatusHistory",
    "DocumentRequirementCatalog",
    "FilingHistory",
    "GoogleOAuthAuthorizationAttempt",
    "GoogleOAuthConnection",
    "LoginAttempt",
    "ObligationCatalog",
    "OperationalTask",
    "ProfessionalFeeAgreement",
    "ProfessionalFeeAgreementHistory",
    "ProfessionalFeeCharge",
    "ProfessionalFeeChargeStatusHistory",
    "ProfessionalFeePayment",
    "ProfessionalFeePaymentHistory",
    "ProfessionalReceipt",
    "SecurityEvent",
    "Session",
    "SolAccessSession",
    "SolDestinationAcceptance",
    "SolAgent",
    "SolAgentJob",
    "SolAgentPairing",
    "SolVault",
    "SolVaultDataKey",
    "SolVaultInvitation",
    "SolVaultKeySlot",
    "TaxAssessment",
    "TaxAssessmentHistory",
    "TaxCategoryCatalog",
    "TaxPayment",
    "TaxPaymentHistory",
    "TaxPeriod",
    "TaxPeriodStatusHistory",
    "TaxScheduleApprovalHistory",
    "TaxScheduleEntry",
    "TaxScheduleException",
    "TaxScheduleImportRun",
    "TaxScheduleSource",
    "TaxScheduleVersion",
    "TaskChangeHistory",
    "TaskComment",
    "TaskGenerationRun",
    "Tenant",
    "User",
]
