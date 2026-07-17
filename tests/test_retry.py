"""Tests for retry logic with exponential backoff."""

import time
from unittest.mock import Mock, patch

import pytest

from sf_utils.exceptions import (
    SalesforceError,
    SalesforceRateLimitError,
    SalesforceAuthError,
    SalesforceAPIError,
)
from sf_utils.retry import (
    RetryConfig,
    APIUsageInfo,
    with_retry,
    raise_for_status,
    DEFAULT_RETRY_CONFIG,
    BATCH_RETRY_CONFIG,
    NO_RETRY_CONFIG,
    _calculate_backoff,
    _consecutive_failures,
)


class TestRetryConfig:
    """Tests for RetryConfig validation."""

    def test_default_config(self):
        """Should create config with default values."""
        config = RetryConfig()
        
        assert config.max_retries == 3
        assert config.initial_backoff == 1.0
        assert config.max_backoff == 60.0
        assert config.jitter == 0.1
        assert config.backoff_multiplier == 2.0

    def test_custom_config(self):
        """Should accept custom values."""
        config = RetryConfig(
            max_retries=5,
            initial_backoff=2.0,
            max_backoff=120.0,
            jitter=0.2,
            backoff_multiplier=3.0,
        )
        
        assert config.max_retries == 5
        assert config.initial_backoff == 2.0
        assert config.max_backoff == 120.0
        assert config.jitter == 0.2
        assert config.backoff_multiplier == 3.0

    def test_validate_max_retries_negative(self):
        """Should reject negative max_retries."""
        with pytest.raises(ValueError, match="max_retries must be >= 0"):
            RetryConfig(max_retries=-1)

    def test_validate_initial_backoff_zero(self):
        """Should reject zero or negative initial_backoff."""
        with pytest.raises(ValueError, match="initial_backoff must be > 0"):
            RetryConfig(initial_backoff=0)

    def test_validate_max_backoff_less_than_initial(self):
        """Should reject max_backoff < initial_backoff."""
        with pytest.raises(ValueError, match="max_backoff must be >= initial_backoff"):
            RetryConfig(initial_backoff=10.0, max_backoff=5.0)

    def test_validate_jitter_out_of_range(self):
        """Should reject jitter outside [0.0, 1.0]."""
        with pytest.raises(ValueError, match="jitter must be between 0.0 and 1.0"):
            RetryConfig(jitter=1.5)

    def test_validate_backoff_multiplier_less_than_one(self):
        """Should reject backoff_multiplier < 1.0."""
        with pytest.raises(ValueError, match="backoff_multiplier must be >= 1.0"):
            RetryConfig(backoff_multiplier=0.5)


class TestPresetConfigs:
    """Tests for preset retry configurations."""

    def test_default_retry_config(self):
        """Should have sensible defaults."""
        assert DEFAULT_RETRY_CONFIG.max_retries == 3
        assert DEFAULT_RETRY_CONFIG.initial_backoff == 1.0
        assert DEFAULT_RETRY_CONFIG.max_backoff == 60.0

    def test_batch_retry_config(self):
        """Should have higher limits for batch operations."""
        assert BATCH_RETRY_CONFIG.max_retries == 5
        assert BATCH_RETRY_CONFIG.max_backoff == 300.0

    def test_no_retry_config(self):
        """Should disable retries."""
        assert NO_RETRY_CONFIG.max_retries == 0


class TestAPIUsageInfo:
    """Tests for API usage parsing."""

    def test_parse_valid_header(self):
        """Should parse api-usage header correctly."""
        info = APIUsageInfo.from_header("api-usage=5000/15000")
        
        assert info is not None
        assert info.used == 5000
        assert info.total == 15000
        assert info.percentage == pytest.approx(33.33, rel=0.01)

    def test_parse_header_with_other_info(self):
        """Should extract api-usage from header with multiple values."""
        info = APIUsageInfo.from_header("per-app-api-usage=100/250; api-usage=12000/15000")
        
        assert info is not None
        assert info.used == 12000
        assert info.total == 15000
        assert info.percentage == 80.0

    def test_parse_invalid_header(self):
        """Should return None for invalid header."""
        info = APIUsageInfo.from_header("invalid-format")
        assert info is None

    def test_parse_none_header(self):
        """Should return None for None header."""
        info = APIUsageInfo.from_header(None)
        assert info is None

    def test_percentage_calculation(self):
        """Should calculate percentage correctly."""
        info = APIUsageInfo.from_header("api-usage=14900/15000")
        assert info.percentage == pytest.approx(99.33, rel=0.01)

    def test_zero_total(self):
        """Should handle zero total gracefully."""
        info = APIUsageInfo(used=0, total=0, percentage=0.0)
        assert info.percentage == 0.0


class TestCalculateBackoff:
    """Tests for backoff calculation."""

    def test_exponential_backoff(self):
        """Should grow exponentially."""
        config = RetryConfig(initial_backoff=1.0, backoff_multiplier=2.0, jitter=0.0)
        
        backoff_0 = _calculate_backoff(0, config)
        backoff_1 = _calculate_backoff(1, config)
        backoff_2 = _calculate_backoff(2, config)
        
        assert backoff_0 == 1.0
        assert backoff_1 == 2.0
        assert backoff_2 == 4.0

    def test_max_backoff_cap(self):
        """Should not exceed max_backoff."""
        config = RetryConfig(initial_backoff=1.0, max_backoff=10.0, jitter=0.0)
        
        backoff_10 = _calculate_backoff(10, config)
        assert backoff_10 == 10.0  # Capped at max_backoff

    def test_retry_after_takes_precedence(self):
        """Should use retry_after when provided."""
        config = RetryConfig(initial_backoff=1.0, jitter=0.0)
        
        backoff = _calculate_backoff(0, config, retry_after=5)
        assert backoff == 5.0

    def test_retry_after_respects_max_backoff(self):
        """Should cap retry_after at max_backoff."""
        config = RetryConfig(max_backoff=10.0, jitter=0.0)
        
        backoff = _calculate_backoff(0, config, retry_after=100)
        assert backoff == 10.0

    def test_jitter_adds_randomness(self):
        """Should add jitter to backoff."""
        config = RetryConfig(initial_backoff=10.0, jitter=0.1, backoff_multiplier=1.0)
        
        # Run multiple times to ensure variance
        backoffs = [_calculate_backoff(0, config) for _ in range(10)]
        
        # All should be within 10% of base (10.0 +/- 1.0)
        for backoff in backoffs:
            assert 9.0 <= backoff <= 11.0
        
        # Should have some variance (not all identical)
        assert len(set(backoffs)) > 1


class TestRaiseForStatus:
    """Tests for centralized error parsing."""

    def test_success_status_no_exception(self):
        """Should not raise for 2xx status codes."""
        raise_for_status({"success": True}, 200)  # Should not raise
        raise_for_status({"success": True}, 201)  # Should not raise

    def test_401_raises_auth_error(self):
        """Should raise SalesforceAuthError for 401."""
        with pytest.raises(SalesforceAuthError) as exc_info:
            raise_for_status({"message": "Unauthorized"}, 401)
        
        assert exc_info.value.status_code == 401
        assert "Unauthorized" in str(exc_info.value)

    def test_403_raises_auth_error(self):
        """Should raise SalesforceAuthError for 403."""
        with pytest.raises(SalesforceAuthError) as exc_info:
            raise_for_status({"message": "Forbidden"}, 403)
        
        assert exc_info.value.status_code == 403

    def test_429_raises_rate_limit_error(self):
        """Should raise SalesforceRateLimitError for 429."""
        with pytest.raises(SalesforceRateLimitError) as exc_info:
            raise_for_status({"message": "Too many requests"}, 429)
        
        assert exc_info.value.status_code == 429

    def test_request_limit_exceeded_raises_rate_limit_error(self):
        """Should raise SalesforceRateLimitError for REQUEST_LIMIT_EXCEEDED."""
        body = [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "Limit exceeded"}]
        
        with pytest.raises(SalesforceRateLimitError) as exc_info:
            raise_for_status(body, 503)
        
        assert "Limit exceeded" in str(exc_info.value)

    def test_rate_limit_with_retry_after_header(self):
        """Should parse Retry-After header."""
        headers = {"Retry-After": "60"}
        
        with pytest.raises(SalesforceRateLimitError) as exc_info:
            raise_for_status({"message": "Rate limited"}, 429, headers)
        
        assert exc_info.value.retry_after == 60

    def test_rate_limit_with_api_usage_header(self):
        """Should parse Sforce-Limit-Info header."""
        headers = {"Sforce-Limit-Info": "api-usage=14000/15000"}
        
        with pytest.raises(SalesforceRateLimitError) as exc_info:
            raise_for_status({"message": "Rate limited"}, 429, headers)
        
        assert exc_info.value.api_usage == "api-usage=14000/15000"

    def test_400_raises_api_error(self):
        """Should raise SalesforceAPIError for 400."""
        with pytest.raises(SalesforceAPIError) as exc_info:
            raise_for_status({"message": "Bad request"}, 400)
        
        assert exc_info.value.status_code == 400

    def test_500_raises_api_error(self):
        """Should raise SalesforceAPIError for 500."""
        with pytest.raises(SalesforceAPIError) as exc_info:
            raise_for_status({"message": "Server error"}, 500)
        
        assert exc_info.value.status_code == 500

    def test_parse_error_from_list_response(self):
        """Should parse error message from list response format."""
        body = [{"message": "Field error", "errorCode": "INVALID_FIELD"}]
        
        with pytest.raises(SalesforceAPIError) as exc_info:
            raise_for_status(body, 400)
        
        assert "Field error" in str(exc_info.value)

    def test_parse_error_from_dict_response(self):
        """Should parse error message from dict response format."""
        body = {"error": "AUTH_ERROR", "error_description": "Invalid token"}
        
        with pytest.raises(SalesforceAuthError) as exc_info:
            raise_for_status(body, 401)
        
        assert "Invalid token" in str(exc_info.value)


class TestWithRetryDecorator:
    """Tests for with_retry decorator."""

    def test_successful_call_no_retry(self):
        """Should return immediately on success."""
        @with_retry(NO_RETRY_CONFIG)
        def successful_func():
            return "success"
        
        result = successful_func()
        assert result == "success"

    def test_auth_error_no_retry(self):
        """Should NOT retry on SalesforceAuthError."""
        call_count = 0
        
        @with_retry(DEFAULT_RETRY_CONFIG)
        def auth_error_func():
            nonlocal call_count
            call_count += 1
            raise SalesforceAuthError("Unauthorized", status_code=401)
        
        with pytest.raises(SalesforceAuthError):
            auth_error_func()
        
        assert call_count == 1  # Only called once, no retries

    @patch('time.sleep')
    def test_rate_limit_retries_with_backoff(self, mock_sleep):
        """Should retry on rate limit with exponential backoff."""
        call_count = 0
        
        @with_retry(RetryConfig(max_retries=2, initial_backoff=1.0, jitter=0.0))
        def rate_limit_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise SalesforceRateLimitError("Rate limited", retry_after=None)
            return "success"
        
        result = rate_limit_func()
        
        assert result == "success"
        assert call_count == 3
        assert mock_sleep.call_count == 2  # Slept twice before success

    @patch('time.sleep')
    def test_rate_limit_respects_retry_after(self, mock_sleep):
        """Should use retry_after from exception."""
        @with_retry(RetryConfig(max_retries=1, jitter=0.0))
        def rate_limit_func():
            raise SalesforceRateLimitError("Rate limited", retry_after=30)
        
        with pytest.raises(SalesforceRateLimitError):
            rate_limit_func()
        
        # Should have slept for ~30 seconds (retry_after value)
        assert mock_sleep.call_count == 1
        assert 25 <= mock_sleep.call_args[0][0] <= 35  # Allow for small variance

    @patch('time.sleep')
    def test_max_retries_exceeded(self, mock_sleep):
        """Should raise after max retries exceeded."""
        call_count = 0
        
        @with_retry(RetryConfig(max_retries=2, initial_backoff=0.1, jitter=0.0))
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise SalesforceRateLimitError("Always fails")
        
        with pytest.raises(SalesforceRateLimitError):
            always_fails()
        
        assert call_count == 3  # Initial call + 2 retries

    @patch('time.sleep')
    def test_500_error_retries(self, mock_sleep):
        """Should retry 5xx errors."""
        call_count = 0
        
        @with_retry(RetryConfig(max_retries=1, initial_backoff=0.1, jitter=0.0))
        def server_error_func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise SalesforceAPIError("Server error", status_code=500)
            return "success"
        
        result = server_error_func()
        assert result == "success"
        assert call_count == 2

    def test_400_error_no_retry(self):
        """Should NOT retry 4xx errors (except rate limits)."""
        call_count = 0
        
        @with_retry(DEFAULT_RETRY_CONFIG)
        def bad_request_func():
            nonlocal call_count
            call_count += 1
            raise SalesforceAPIError("Bad request", status_code=400)
        
        with pytest.raises(SalesforceAPIError):
            bad_request_func()
        
        assert call_count == 1  # No retries for 4xx

    @patch('time.sleep')
    def test_api_usage_warning(self, mock_sleep, caplog):
        """Should warn when API usage exceeds 80%."""
        @with_retry(RetryConfig(max_retries=0))
        def high_usage_func():
            raise SalesforceRateLimitError(
                "Rate limited",
                api_usage="api-usage=14500/15000"  # 96.67%
            )
        
        with pytest.raises(SalesforceRateLimitError):
            high_usage_func()
        
        assert "API usage critical" in caplog.text
        assert "96" in caplog.text

    @patch('sf_utils.retry._consecutive_failures', 0)
    def test_circuit_breaker_trips(self):
        """Should trip circuit breaker after consecutive failures."""
        import sf_utils.retry
        
        @with_retry(RetryConfig(max_retries=0))
        def failing_func():
            raise SalesforceAPIError("Error", status_code=500)
        
        # Trigger 10 consecutive failures
        for i in range(10):
            try:
                failing_func()
            except SalesforceAPIError:
                pass
        
        # 11th call should trip circuit breaker
        with pytest.raises(SalesforceError, match="Circuit breaker"):
            failing_func()

    @patch('sf_utils.retry._consecutive_failures', 0)
    @patch('time.sleep')
    def test_circuit_breaker_resets_on_success(self, mock_sleep):
        """Should reset circuit breaker after successful call."""
        import sf_utils.retry
        
        call_count = 0
        
        @with_retry(RetryConfig(max_retries=1, initial_backoff=0.1, jitter=0.0))
        def sometimes_fails():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise SalesforceAPIError("Error", status_code=500)
            return "success"
        
        result = sometimes_fails()
        assert result == "success"
        
        # Circuit breaker should be reset after success
        assert sf_utils.retry._consecutive_failures == 0
