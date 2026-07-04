from .circular_reference import CircularReferenceRule
from .type_mismatch import TypeMismatchRule
from .overwrite_referenced_cell import OverwriteReferencedCellRule
from .date_format_check import DateFormatCheckRule
from .email_format_check import EmailFormatCheckRule
from .percentage_range_check import PercentageRangeCheckRule
from .forecast_sanity import ForecastSanityRule
from .forecast_integrity import ForecastIntegrityRule
from .financial import (
    TgrVsWaccRule,
    WaccRangeRule,
    WaccHardcodedRule,
    TaxRateRangeRule,
    TaxRateReasonableRule,
    HorizontalFormulaConsistencyRule,
    HardcodeTrendAnomalyRule,
    DebtEquitySumRule,
    BetaRangeRule,
    TgrVsGdpRule,
)

RULES = [
    # Type 1: structural checks
    CircularReferenceRule(),
    TypeMismatchRule(),
    OverwriteReferencedCellRule(),
    # Type 2: header-convention checks
    DateFormatCheckRule(),
    EmailFormatCheckRule(),
    PercentageRangeCheckRule(),
    # Type 3: forecast acceptance-range check
    ForecastSanityRule(),
    # Type 4: forecast declared-vs-actual integrity (history provenance,
    # rate reconciliation, method guardrail)
    ForecastIntegrityRule(),
]

RULES_REVIEW = [
    TgrVsWaccRule(),
    WaccRangeRule(),
    WaccHardcodedRule(),
    TaxRateRangeRule(),
    TaxRateReasonableRule(),
    HorizontalFormulaConsistencyRule(),
    HardcodeTrendAnomalyRule(),
    DebtEquitySumRule(),
    BetaRangeRule(),
    TgrVsGdpRule(),
]
