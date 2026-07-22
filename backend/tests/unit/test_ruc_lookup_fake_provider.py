import pytest

from app.domain.ruc_lookup.fake_provider import FakeRucLookupProvider
from app.domain.ruc_lookup.sunat_provider import SunatRucLookupProvider
from app.services.ruc_validator import compute_check_digit

_FICTITIOUS_RUC = "20" + "12345678" + str(compute_check_digit("2012345678"))


def test_fake_provider_is_deterministic() -> None:
    provider = FakeRucLookupProvider()
    result_a = provider.lookup(_FICTITIOUS_RUC)
    result_b = provider.lookup(_FICTITIOUS_RUC)
    assert result_a.business_name == result_b.business_name
    assert result_a.district == result_b.district


def test_fake_provider_marks_result_as_simulated() -> None:
    provider = FakeRucLookupProvider()
    result = provider.lookup(_FICTITIOUS_RUC)
    assert result.is_simulated is True
    assert result.ruc == _FICTITIOUS_RUC


def test_sunat_provider_is_not_implemented() -> None:
    provider = SunatRucLookupProvider()
    with pytest.raises(NotImplementedError):
        provider.lookup(_FICTITIOUS_RUC)
