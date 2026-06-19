from .circular_reference import CircularReferenceRule
from .type_mismatch import TypeMismatchRule
from .overwrite_referenced_cell import OverwriteReferencedCellRule
from .date_format_check import DateFormatCheckRule
from .email_format_check import EmailFormatCheckRule
from .percentage_range_check import PercentageRangeCheckRule
from .forecast_sanity import ForecastSanityRule
from .financial import (
    TgrVsWaccRule,
    WaccRangeRule,
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
]

RULES_REVIEW = [
    TgrVsWaccRule(),
    WaccRangeRule(),
    TaxRateRangeRule(),
    TaxRateReasonableRule(),
    HorizontalFormulaConsistencyRule(),
    HardcodeTrendAnomalyRule(),
    DebtEquitySumRule(),
    BetaRangeRule(),
    TgrVsGdpRule(),
]
