"""A CAS is somebody's entire financial position. These tests exist to make the
privacy posture non-negotiable rather than aspirational."""
from serving.api.services.cas_parser import CASHolding, CASResult, redact


def test_pan_is_redacted():
    assert "ABCDE1234F" not in redact("PAN: ABCDE1234F held by investor")
    assert "[PAN REDACTED]" in redact("PAN: ABCDE1234F")


def test_email_is_redacted():
    out = redact("Email: aadarsh.senapati2005@gmail.com")
    assert "@gmail.com" not in out
    assert "[EMAIL REDACTED]" in out


def test_folio_is_redacted():
    assert "12345678/90" not in redact("Folio No: 12345678/90")


def test_only_isin_and_value_leave_the_module():
    r = CASResult(holdings=[CASHolding("INE040A01034", "Some Fund", 100.5, 402.48, 40449.0)])
    portfolio = r.to_portfolio()
    assert portfolio == {"INE040A01034": 40449.0}
    assert all(isinstance(v, float) for v in portfolio.values())
