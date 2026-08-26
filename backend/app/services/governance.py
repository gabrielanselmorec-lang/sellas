from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


LGPD_GOVERNANCE_CHECKLIST = [
    "Confirmar base legal e finalidade especifica para uso preditivo.",
    "Separar controlador, operador, encarregado e responsabilidades de auditoria.",
    "Usar pseudonimizacao/anonimizacao sempre que possivel.",
    "Registrar consentimentos, contratos e politicas de retencao.",
    "Executar DPIA/RIPD antes de uso com dados reais sensiveis.",
    "Validar riscos de vies, estigmatizacao, restricao indevida e falsos negativos.",
    "Bloquear uso como decisao automatizada sem revisao humana qualificada.",
    "Avaliar enquadramento regulatorio como SaMD/software medico antes de producao.",
]


def governance_status() -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_use_allowed": False,
        "clinical_disclaimer": "Ferramenta de apoio; nao substitui avaliacao funcional nem julgamento clinico.",
        "lgpd_checklist": LGPD_GOVERNANCE_CHECKLIST,
        "required_before_real_use": [
            "parecer juridico LGPD",
            "governanca clinica documentada",
            "validacao prospectiva silenciosa",
            "analise regulatoria SaMD",
            "controle de acesso e trilha de auditoria imutavel",
        ],
        "runtime_gates": {
            "clinical_mode_allowed": False,
            "requires_rbac": True,
            "requires_immutable_audit_log": True,
            "requires_model_card": True,
            "requires_silent_prospective_validation": True,
            "blocks_automated_intervention": True,
        },
    }
