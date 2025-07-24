"""
Error Reporter Service for comprehensive error tracking and reporting

Provides centralized error collection, categorization, and reporting
with support for graceful degradation and recovery suggestions.
"""
import logging
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import traceback
import json
from collections import defaultdict, deque

logger = logging.getLogger(__name__)


class ErrorSeverity(str, Enum):
    """Error severity levels"""
    LOW = "low"          # Recoverable, minimal impact
    MEDIUM = "medium"    # Recoverable with degraded functionality
    HIGH = "high"        # Major functionality impacted
    CRITICAL = "critical"  # System functionality compromised


class ErrorCategory(str, Enum):
    """Error categories for classification"""
    NETWORK = "network"
    AUTHENTICATION = "authentication"
    RESOURCE = "resource"
    VALIDATION = "validation"
    EXTERNAL_SERVICE = "external_service"
    INTERNAL = "internal"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"


@dataclass
class ErrorReport:
    """Individual error report"""
    error_id: str
    timestamp: datetime
    category: ErrorCategory
    severity: ErrorSeverity
    component: str
    operation: str
    error_type: str
    error_message: str
    stack_trace: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    recovery_suggestions: List[str] = field(default_factory=list)
    user_message: Optional[str] = None


@dataclass
class ErrorStats:
    """Error statistics for a component"""
    total_errors: int = 0
    errors_by_category: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    errors_by_severity: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    recent_errors: deque = field(default_factory=lambda: deque(maxlen=100))
    last_error_time: Optional[datetime] = None
    error_rate_per_minute: float = 0.0


class ErrorReporter:
    """
    Centralized error reporting and analysis service
    
    Features:
    - Error categorization and severity assessment
    - Recovery suggestions based on error type
    - Rate limiting detection
    - Graceful degradation recommendations
    - Error trend analysis
    """
    
    def __init__(
        self,
        max_errors_per_component: int = 1000,
        error_retention_hours: int = 24,
        alert_threshold_per_minute: float = 10.0
    ):
        self.max_errors_per_component = max_errors_per_component
        self.error_retention_delta = timedelta(hours=error_retention_hours)
        self.alert_threshold = alert_threshold_per_minute
        
        # Error storage
        self.errors_by_component: Dict[str, List[ErrorReport]] = defaultdict(list)
        self.error_stats: Dict[str, ErrorStats] = defaultdict(ErrorStats)
        self.global_error_count = 0
        
        # Alert state
        self.alert_sent: Dict[str, datetime] = {}
        self.alert_cooldown = timedelta(minutes=5)
        
        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_old_errors())
        
        logger.info(f"📊 Error Reporter initialized with retention={error_retention_hours}h, alert_threshold={alert_threshold_per_minute}/min")
    
    async def report_error(
        self,
        component: str,
        operation: str,
        error: Exception,
        severity: Optional[ErrorSeverity] = None,
        context: Optional[Dict[str, Any]] = None,
        user_message: Optional[str] = None
    ) -> ErrorReport:
        """
        Report an error with automatic categorization and suggestions
        
        Args:
            component: Component where error occurred (e.g., "trigger_endpoint")
            operation: Operation being performed (e.g., "create_room")
            error: The exception that occurred
            severity: Override automatic severity detection
            context: Additional context information
            user_message: User-friendly error message
        
        Returns:
            ErrorReport with recovery suggestions
        """
        # Auto-detect category and severity if not provided
        category = self._categorize_error(error)
        if severity is None:
            severity = self._assess_severity(error, category)
        
        # Generate error ID
        error_id = f"{component}_{operation}_{int(datetime.now().timestamp())}"
        
        # Get stack trace
        stack_trace = traceback.format_exc()
        
        # Generate recovery suggestions
        suggestions = self._generate_recovery_suggestions(error, category)
        
        # Create user-friendly message if not provided
        if user_message is None:
            user_message = self._generate_user_message(error, category, operation)
        
        # Create error report
        report = ErrorReport(
            error_id=error_id,
            timestamp=datetime.now(),
            category=category,
            severity=severity,
            component=component,
            operation=operation,
            error_type=type(error).__name__,
            error_message=str(error),
            stack_trace=stack_trace,
            context=context or {},
            recovery_suggestions=suggestions,
            user_message=user_message
        )
        
        # Store error
        await self._store_error(report)
        
        # Log based on severity
        if severity == ErrorSeverity.CRITICAL:
            logger.critical(f"🚨 CRITICAL ERROR in {component}.{operation}: {error}")
        elif severity == ErrorSeverity.HIGH:
            logger.error(f"❌ HIGH severity error in {component}.{operation}: {error}")
        elif severity == ErrorSeverity.MEDIUM:
            logger.warning(f"⚠️ MEDIUM severity error in {component}.{operation}: {error}")
        else:
            logger.info(f"ℹ️ LOW severity error in {component}.{operation}: {error}")
        
        # Check if we should send alerts
        await self._check_alert_conditions(component)
        
        return report
    
    def _categorize_error(self, error: Exception) -> ErrorCategory:
        """Automatically categorize error based on type and message"""
        error_msg = str(error).lower()
        error_type = type(error).__name__
        
        # Network-related errors
        if any(keyword in error_msg for keyword in ['connection', 'network', 'refused', 'unreachable', 'dns']):
            return ErrorCategory.NETWORK
        
        # Authentication errors
        if any(keyword in error_msg for keyword in ['auth', 'unauthorized', 'forbidden', 'credential', 'token']):
            return ErrorCategory.AUTHENTICATION
        
        # Resource errors
        if any(keyword in error_msg for keyword in ['memory', 'disk', 'cpu', 'quota', 'limit exceeded', 'capacity']):
            return ErrorCategory.RESOURCE
        
        # Timeout errors
        if any(keyword in error_msg for keyword in ['timeout', 'timed out', 'deadline']):
            return ErrorCategory.TIMEOUT
        
        # Rate limiting
        if any(keyword in error_msg for keyword in ['rate limit', 'too many requests', 'throttled']):
            return ErrorCategory.RATE_LIMIT
        
        # Validation errors
        if any(keyword in error_msg for keyword in ['invalid', 'validation', 'bad request', 'malformed']):
            return ErrorCategory.VALIDATION
        
        # External service errors
        if any(keyword in error_msg for keyword in ['external', 'third party', 'api error', 'service unavailable']):
            return ErrorCategory.EXTERNAL_SERVICE
        
        # Default to internal
        return ErrorCategory.INTERNAL
    
    def _assess_severity(self, error: Exception, category: ErrorCategory) -> ErrorSeverity:
        """Assess error severity based on type and category"""
        # Critical severity patterns
        if category == ErrorCategory.AUTHENTICATION:
            return ErrorSeverity.HIGH
        
        if category == ErrorCategory.RESOURCE:
            return ErrorSeverity.CRITICAL
        
        # High severity patterns
        if category in [ErrorCategory.NETWORK, ErrorCategory.EXTERNAL_SERVICE]:
            return ErrorSeverity.HIGH
        
        # Medium severity patterns
        if category in [ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMIT]:
            return ErrorSeverity.MEDIUM
        
        # Low severity patterns
        if category == ErrorCategory.VALIDATION:
            return ErrorSeverity.LOW
        
        # Default based on exception type
        if isinstance(error, (ValueError, KeyError, TypeError)):
            return ErrorSeverity.LOW
        
        return ErrorSeverity.MEDIUM
    
    def _generate_recovery_suggestions(self, error: Exception, category: ErrorCategory) -> List[str]:
        """Generate recovery suggestions based on error type"""
        suggestions = []
        
        if category == ErrorCategory.NETWORK:
            suggestions.extend([
                "Check network connectivity",
                "Verify service endpoints are accessible",
                "Consider implementing retry logic with exponential backoff",
                "Check firewall and security group settings"
            ])
        
        elif category == ErrorCategory.AUTHENTICATION:
            suggestions.extend([
                "Verify API credentials are correct",
                "Check token expiration",
                "Ensure proper permissions are granted",
                "Regenerate credentials if compromised"
            ])
        
        elif category == ErrorCategory.RESOURCE:
            suggestions.extend([
                "Scale up resources (CPU/Memory)",
                "Implement resource pooling",
                "Add resource monitoring and alerts",
                "Optimize resource usage in code"
            ])
        
        elif category == ErrorCategory.TIMEOUT:
            suggestions.extend([
                "Increase timeout values",
                "Optimize slow operations",
                "Implement request queuing",
                "Add circuit breakers for failing services"
            ])
        
        elif category == ErrorCategory.RATE_LIMIT:
            suggestions.extend([
                "Implement request throttling",
                "Use exponential backoff for retries",
                "Consider upgrading API limits",
                "Distribute requests over time"
            ])
        
        elif category == ErrorCategory.VALIDATION:
            suggestions.extend([
                "Validate input data before processing",
                "Add comprehensive error messages",
                "Implement input sanitization",
                "Update API documentation"
            ])
        
        return suggestions
    
    def _generate_user_message(self, error: Exception, category: ErrorCategory, operation: str) -> str:
        """Generate user-friendly error message"""
        base_messages = {
            ErrorCategory.NETWORK: "We're having trouble connecting to our services. Please try again in a moment.",
            ErrorCategory.AUTHENTICATION: "There was an authentication issue. Please check your credentials.",
            ErrorCategory.RESOURCE: "Our system is experiencing high load. Please try again later.",
            ErrorCategory.TIMEOUT: f"The {operation} operation took too long. Please try again.",
            ErrorCategory.RATE_LIMIT: "You've made too many requests. Please wait a moment before trying again.",
            ErrorCategory.VALIDATION: "The provided information appears to be invalid. Please check and try again.",
            ErrorCategory.EXTERNAL_SERVICE: "One of our partner services is temporarily unavailable.",
            ErrorCategory.INTERNAL: f"An unexpected error occurred during {operation}. Our team has been notified."
        }
        
        return base_messages.get(category, f"An error occurred during {operation}. Please try again.")
    
    async def _store_error(self, report: ErrorReport):
        """Store error report and update statistics"""
        component = report.component
        
        # Store error
        self.errors_by_component[component].append(report)
        self.global_error_count += 1
        
        # Update stats
        stats = self.error_stats[component]
        stats.total_errors += 1
        stats.errors_by_category[report.category] += 1
        stats.errors_by_severity[report.severity] += 1
        stats.recent_errors.append(report)
        stats.last_error_time = report.timestamp
        
        # Calculate error rate
        now = datetime.now()
        recent_count = sum(1 for err in stats.recent_errors 
                          if (now - err.timestamp).total_seconds() < 60)
        stats.error_rate_per_minute = recent_count
        
        # Trim errors if exceeded limit
        if len(self.errors_by_component[component]) > self.max_errors_per_component:
            self.errors_by_component[component] = self.errors_by_component[component][-self.max_errors_per_component:]
    
    async def _check_alert_conditions(self, component: str):
        """Check if alert conditions are met"""
        stats = self.error_stats[component]
        
        # Check error rate
        if stats.error_rate_per_minute > self.alert_threshold:
            # Check cooldown
            last_alert = self.alert_sent.get(component)
            if not last_alert or (datetime.now() - last_alert) > self.alert_cooldown:
                logger.critical(f"🚨 ALERT: Error rate for {component} is {stats.error_rate_per_minute:.1f}/min (threshold: {self.alert_threshold}/min)")
                self.alert_sent[component] = datetime.now()
                
                # Log top error categories
                top_categories = sorted(stats.errors_by_category.items(), 
                                      key=lambda x: x[1], reverse=True)[:3]
                logger.critical(f"Top error categories: {top_categories}")
    
    async def _cleanup_old_errors(self):
        """Periodically clean up old errors"""
        while True:
            try:
                await asyncio.sleep(3600)  # Run every hour
                
                cutoff_time = datetime.now() - self.error_retention_delta
                cleaned_count = 0
                
                for component in list(self.errors_by_component.keys()):
                    errors = self.errors_by_component[component]
                    new_errors = [err for err in errors if err.timestamp > cutoff_time]
                    cleaned_count += len(errors) - len(new_errors)
                    self.errors_by_component[component] = new_errors
                
                if cleaned_count > 0:
                    logger.info(f"🧹 Cleaned up {cleaned_count} old errors")
                    
            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")
    
    def get_error_summary(self, component: Optional[str] = None) -> Dict[str, Any]:
        """Get error summary for a component or all components"""
        if component:
            stats = self.error_stats[component]
            return {
                "component": component,
                "total_errors": stats.total_errors,
                "error_rate_per_minute": stats.error_rate_per_minute,
                "last_error_time": stats.last_error_time.isoformat() if stats.last_error_time else None,
                "errors_by_category": dict(stats.errors_by_category),
                "errors_by_severity": dict(stats.errors_by_severity),
                "recent_errors": [
                    {
                        "timestamp": err.timestamp.isoformat(),
                        "category": err.category,
                        "severity": err.severity,
                        "operation": err.operation,
                        "user_message": err.user_message
                    }
                    for err in list(stats.recent_errors)[-10:]  # Last 10 errors
                ]
            }
        else:
            # Global summary
            return {
                "global_error_count": self.global_error_count,
                "components_with_errors": len(self.error_stats),
                "components": {
                    comp: self.get_error_summary(comp)
                    for comp in self.error_stats.keys()
                }
            }
    
    def get_recovery_plan(self, component: str) -> Dict[str, Any]:
        """Get recovery plan based on recent errors"""
        stats = self.error_stats[component]
        if not stats.recent_errors:
            return {"status": "healthy", "suggestions": []}
        
        # Analyze recent errors
        category_counts = defaultdict(int)
        all_suggestions = set()
        
        for err in stats.recent_errors:
            category_counts[err.category] += 1
            all_suggestions.update(err.recovery_suggestions)
        
        # Determine primary issue
        primary_category = max(category_counts.items(), key=lambda x: x[1])[0]
        
        return {
            "status": "degraded" if stats.error_rate_per_minute > 5 else "recovering",
            "primary_issue": primary_category,
            "error_rate": stats.error_rate_per_minute,
            "suggestions": list(all_suggestions)[:5]  # Top 5 suggestions
        }


# Global error reporter instance
_error_reporter: Optional[ErrorReporter] = None


def get_error_reporter() -> ErrorReporter:
    """Get the global error reporter instance"""
    global _error_reporter
    if _error_reporter is None:
        _error_reporter = ErrorReporter()
    return _error_reporter


async def report_error(
    component: str,
    operation: str,
    error: Exception,
    **kwargs
) -> ErrorReport:
    """Convenience function to report errors"""
    reporter = get_error_reporter()
    return await reporter.report_error(component, operation, error, **kwargs)