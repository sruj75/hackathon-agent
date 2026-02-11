"""
Unit tests for cron_service.py

Tests:
- Dynamic cron job creation via cron-jobs.org API
- Job cleanup/deletion
- Error handling
- API payload formatting
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

import cron_service


class TestCronService:
    """Test suite for cron service."""

    @pytest.mark.asyncio
    async def test_create_one_time_job_success(self, mock_cron_api):
        """Test successful one-time job creation."""
        target_datetime = datetime(2026, 2, 5, 8, 0, 0)
        event_id = "test_event_123"
        
        # Mock successful response
        mock_cron_api.put.return_value.json.return_value = {
            "jobId": 12345,
            "status": "OK"
        }
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            with patch('cron_service.BACKEND_URL', 'https://test.example.com'):
                job_id = await cron_service.create_one_time_job(
                    target_datetime=target_datetime,
                    event_id=event_id,
                    timezone="UTC"
                )
        
        assert job_id == 12345
        
        # Verify API call
        mock_cron_api.put.assert_called_once()
        call_args = mock_cron_api.put.call_args
        
        # Verify URL
        assert call_args.args[0] == "https://api.cron-job.org/jobs"
        
        # Verify payload structure
        payload = call_args.kwargs['json']
        assert payload['job']['url'] == f"https://test.example.com/api/execute-event/{event_id}"
        assert payload['job']['enabled'] is True
        assert payload['job']['schedule']['timezone'] == "UTC"
        assert payload['job']['schedule']['hours'] == [8]
        assert payload['job']['schedule']['minutes'] == [0]
        assert payload['job']['schedule']['mdays'] == [5]
        assert payload['job']['schedule']['months'] == [2]
        
        # Verify expiration is set (5 minutes after target)
        expires_at = int((target_datetime + timedelta(minutes=5)).strftime("%Y%m%d%H%M%S"))
        assert payload['job']['schedule']['expiresAt'] == expires_at
        
        # Verify headers
        headers = call_args.kwargs['headers']
        assert headers['Authorization'] == 'Bearer test_api_key'
        assert headers['Content-Type'] == 'application/json'

    @pytest.mark.asyncio
    async def test_create_one_time_job_with_timezone(self, mock_cron_api):
        """Test job creation with non-UTC timezone."""
        target_datetime = datetime(2026, 2, 5, 14, 30, 0)
        event_id = "test_event_tz"
        
        mock_cron_api.put.return_value.json.return_value = {
            "jobId": 54321
        }
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            job_id = await cron_service.create_one_time_job(
                target_datetime=target_datetime,
                event_id=event_id,
                timezone="America/New_York"
            )
        
        assert job_id == 54321
        
        # Verify timezone in payload
        call_args = mock_cron_api.put.call_args
        payload = call_args.kwargs['json']
        assert payload['job']['schedule']['timezone'] == "America/New_York"

    @pytest.mark.asyncio
    async def test_create_one_time_job_no_api_key(self):
        """Test that missing API key raises error."""
        target_datetime = datetime(2026, 2, 5, 8, 0, 0)
        event_id = "test_event"
        
        with patch('cron_service.CRONJOB_API_KEY', None):
            with pytest.raises(ValueError, match="CRONJOB_ORG_API_KEY"):
                await cron_service.create_one_time_job(
                    target_datetime=target_datetime,
                    event_id=event_id,
                    timezone="UTC",
                )

    @pytest.mark.asyncio
    async def test_create_one_time_job_api_error(self, mock_cron_api):
        """Test handling of API errors."""
        target_datetime = datetime(2026, 2, 5, 8, 0, 0)
        event_id = "test_event"
        
        # Mock API error
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"
        
        mock_cron_api.put.side_effect = httpx.HTTPStatusError(
            "Server error",
            request=MagicMock(),
            response=error_response
        )
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            with pytest.raises(Exception, match="Failed to create cron job"):
                await cron_service.create_one_time_job(
                    target_datetime=target_datetime,
                    event_id=event_id,
                    timezone="UTC",
                )

    @pytest.mark.asyncio
    async def test_create_one_time_job_missing_job_id_in_response(self, mock_cron_api):
        """Test handling of malformed API response."""
        target_datetime = datetime(2026, 2, 5, 8, 0, 0)
        event_id = "test_event"
        
        # Mock response without jobId
        mock_cron_api.put.return_value.json.return_value = {
            "status": "OK"
            # Missing jobId
        }
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            with pytest.raises(ValueError, match="No jobId in response"):
                await cron_service.create_one_time_job(
                    target_datetime=target_datetime,
                    event_id=event_id,
                    timezone="UTC",
                )

    @pytest.mark.asyncio
    async def test_create_one_time_job_timeout(self, mock_httpx_client):
        """Test handling of timeout errors."""
        target_datetime = datetime(2026, 2, 5, 8, 0, 0)
        event_id = "test_event"
        
        # Mock timeout
        mock_httpx_client.put.side_effect = httpx.TimeoutException("Timeout")
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            with pytest.raises(Exception):
                await cron_service.create_one_time_job(
                    target_datetime=target_datetime,
                    event_id=event_id,
                    timezone="UTC",
                )

    @pytest.mark.asyncio
    async def test_delete_job_success(self, mock_cron_api):
        """Test successful job deletion."""
        job_id = 12345
        
        mock_cron_api.delete.return_value.status_code = 200
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            result = await cron_service.delete_job(job_id)
        
        assert result is True
        
        # Verify API call
        mock_cron_api.delete.assert_called_once()
        call_args = mock_cron_api.delete.call_args
        assert call_args.args[0] == f"https://api.cron-job.org/jobs/{job_id}"
        
        # Verify headers
        headers = call_args.kwargs['headers']
        assert headers['Authorization'] == 'Bearer test_api_key'

    @pytest.mark.asyncio
    async def test_delete_job_already_deleted(self, mock_cron_api):
        """Test that 404 on deletion is treated as success."""
        job_id = 12345
        
        # Mock 404 response
        error_response = MagicMock()
        error_response.status_code = 404
        error_response.text = "Not Found"
        
        mock_cron_api.delete.side_effect = httpx.HTTPStatusError(
            "Not found",
            request=MagicMock(),
            response=error_response
        )
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            result = await cron_service.delete_job(job_id)
        
        # 404 should be treated as success (already deleted)
        assert result is True

    @pytest.mark.asyncio
    async def test_delete_job_no_api_key(self):
        """Test that missing API key returns False."""
        job_id = 12345
        
        with patch('cron_service.CRONJOB_API_KEY', None):
            result = await cron_service.delete_job(job_id)
        
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_job_no_job_id(self):
        """Test that missing job_id returns False."""
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            result = await cron_service.delete_job(None)
        
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_job_api_error(self, mock_cron_api):
        """Test handling of API errors during deletion."""
        job_id = 12345
        
        # Mock API error
        error_response = MagicMock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"
        
        mock_cron_api.delete.side_effect = httpx.HTTPStatusError(
            "Server error",
            request=MagicMock(),
            response=error_response
        )
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            result = await cron_service.delete_job(job_id)
        
        assert result is False

    @pytest.mark.asyncio
    async def test_delete_job_network_error(self, mock_cron_api):
        """Test handling of network errors during deletion."""
        job_id = 12345
        
        mock_cron_api.delete.side_effect = Exception("Network error")
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            result = await cron_service.delete_job(job_id)
        
        assert result is False

    @pytest.mark.asyncio
    async def test_cron_expression_calculation(self, mock_cron_api):
        """Test that datetime is correctly converted to cron fields."""
        # Test various datetimes
        test_cases = [
            (datetime(2026, 1, 1, 0, 0, 0), [0], [0], [1], [1]),  # New Year midnight
            (datetime(2026, 12, 31, 23, 59, 0), [23], [59], [31], [12]),  # New Year's Eve
            (datetime(2026, 6, 15, 12, 30, 0), [12], [30], [15], [6]),  # Mid-year afternoon
        ]
        
        mock_cron_api.put.return_value.json.return_value = {"jobId": 999}
        
        for target_dt, exp_hours, exp_minutes, exp_mdays, exp_months in test_cases:
            with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
                await cron_service.create_one_time_job(
                    target_datetime=target_dt,
                    event_id="test",
                    timezone="UTC",
                )
            
            call_args = mock_cron_api.put.call_args
            payload = call_args.kwargs['json']
            schedule = payload['job']['schedule']
            
            assert schedule['hours'] == exp_hours
            assert schedule['minutes'] == exp_minutes
            assert schedule['mdays'] == exp_mdays
            assert schedule['months'] == exp_months

    @pytest.mark.asyncio
    async def test_job_expiration_set_correctly(self, mock_cron_api):
        """Test that expiresAt is set to 5 minutes after target."""
        target_datetime = datetime(2026, 2, 4, 10, 30, 0)
        
        mock_cron_api.put.return_value.json.return_value = {"jobId": 123}
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            await cron_service.create_one_time_job(
                target_datetime=target_datetime,
                event_id="test",
                timezone="UTC",
            )
        
        call_args = mock_cron_api.put.call_args
        payload = call_args.kwargs['json']
        expires_at = payload['job']['schedule']['expiresAt']
        
        # Expected: 10:35:00 on 2026-02-04
        expected_expires = int(datetime(2026, 2, 4, 10, 35, 0).strftime("%Y%m%d%H%M%S"))
        assert expires_at == expected_expires

    @pytest.mark.asyncio
    async def test_callback_url_includes_event_id(self, mock_cron_api):
        """Test that callback URL is correctly formatted with event ID."""
        event_id = "event_abc123xyz"
        target_datetime = datetime(2026, 2, 4, 8, 0, 0)
        
        mock_cron_api.put.return_value.json.return_value = {"jobId": 456}
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            with patch('cron_service.BACKEND_URL', 'https://my-backend.com'):
                await cron_service.create_one_time_job(
                    target_datetime=target_datetime,
                    event_id=event_id,
                    timezone="UTC",
                )
        
        call_args = mock_cron_api.put.call_args
        payload = call_args.kwargs['json']
        url = payload['job']['url']
        
        assert url == f"https://my-backend.com/api/execute-event/{event_id}"

    @pytest.mark.asyncio
    async def test_request_method_is_post(self, mock_cron_api):
        """Test that cron job uses POST method."""
        target_datetime = datetime(2026, 2, 4, 8, 0, 0)
        
        mock_cron_api.put.return_value.json.return_value = {"jobId": 789}
        
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            await cron_service.create_one_time_job(
                target_datetime=target_datetime,
                event_id="test",
                timezone="UTC",
            )

        call_args = mock_cron_api.put.call_args
        payload = call_args.kwargs['json']
        
        # requestMethod 1 = POST
        assert payload['job']['requestMethod'] == 1

    @pytest.mark.asyncio
    async def test_create_one_time_job_missing_timezone(self):
        """Missing timezone should fail fast."""
        target_datetime = datetime(2026, 2, 5, 8, 0, 0)
        with patch('cron_service.CRONJOB_API_KEY', 'test_api_key'):
            with pytest.raises(ValueError, match="missing_timezone"):
                await cron_service.create_one_time_job(
                    target_datetime=target_datetime,
                    event_id="test_event",
                    timezone="",
                )
