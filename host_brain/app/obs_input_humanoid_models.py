"""Typed response models for Input/Humanoid observability routes."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class InputHumanoidLoopStageItem(BaseModel):
    stage: str
    count: int
    rate: Optional[float]


class InputHumanoidLoopStatsResponse(BaseModel):
    window_minutes: int
    total_gateway_requests: int
    completed_requests: int
    failed_requests: int
    ack_touched_requests: int
    ack_confirmed_requests: int
    progress_touched_requests: int
    deviation_detected_requests: int
    deviation_recovered_requests: int
    closed_loop_requests: int
    completion_rate: Optional[float]
    ack_touch_rate: Optional[float]
    progress_touch_rate: Optional[float]
    deviation_recovery_rate: Optional[float]
    closed_loop_rate: Optional[float]
    stage_counts: List[InputHumanoidLoopStageItem]
    timestamp: int


class InputHumanoidLoopTrendResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    timeseries_labels: List[str]
    timeseries_total_gateway_requests: List[int]
    timeseries_completed_requests: List[int]
    timeseries_failed_requests: List[int]
    timeseries_closed_loop_requests: List[int]
    timeseries_ack_touched_requests: List[int]
    timeseries_progress_touched_requests: List[int]
    timeseries_deviation_detected_requests: List[int]
    timeseries_deviation_recovered_requests: List[int]
    timeseries_completion_rate: List[Optional[float]]
    timeseries_closed_loop_rate: List[Optional[float]]
    timeseries_ack_touch_rate: List[Optional[float]]
    timeseries_progress_touch_rate: List[Optional[float]]
    timeseries_deviation_recovery_rate: List[Optional[float]]
    timestamp: int


class InputHumanoidLoopAlertItem(BaseModel):
    code: str
    severity: str
    title: str
    detail: str
    metric: str
    value: Optional[float]
    threshold: Optional[float]
    direction: str


class InputHumanoidLoopAlertsResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    evaluated_bucket_index: Optional[int]
    evaluated_label: Optional[str]
    sample_requests: int
    status: str
    severity: str
    summary: str
    latest_completion_rate: Optional[float]
    latest_closed_loop_rate: Optional[float]
    latest_ack_touch_rate: Optional[float]
    latest_progress_touch_rate: Optional[float]
    latest_failed_rate: Optional[float]
    latest_deviation_recovery_rate: Optional[float]
    alerts: List[InputHumanoidLoopAlertItem]
    timestamp: int


class InputHumanoidLoopRemediationStepItem(BaseModel):
    step_id: str
    priority: int
    title: str
    detail: str
    category: str
    target_path: Optional[str]
    target_label: Optional[str]
    operator_hint: Optional[str]
    evidence_codes: List[str]


class InputHumanoidLoopRemediationRequestItem(BaseModel):
    request_id: str
    category: str
    reason: str
    target_path: str


class InputHumanoidLoopRemediationResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    status: str
    severity: str
    summary: str
    primary_focus: str
    next_review_hint: str
    representative_requests: List[InputHumanoidLoopRemediationRequestItem]
    steps: List[InputHumanoidLoopRemediationStepItem]
    timestamp: int


class InputHumanoidLoopDiagnosticItem(BaseModel):
    request_id: str
    category: str
    status: str
    input_text: Optional[str]
    output_text: Optional[str]
    suspected_breakpoint: str
    root_cause_code: str
    root_cause_summary: str
    reason_hint: Optional[str]
    evidence_codes: List[str]
    recommended_view_path: str
    recommended_view_label: str


class InputHumanoidLoopDiagnosticsResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    status: str
    severity: str
    summary: str
    diagnosed_count: int
    diagnostics: List[InputHumanoidLoopDiagnosticItem]
    timestamp: int


class InputHumanoidLoopHotspotRequestItem(BaseModel):
    request_id: str
    status: str
    target_path: str


class InputHumanoidLoopHotspotItem(BaseModel):
    hotspot_id: str
    organ_system: str
    organ_label: str
    root_cause_code: str
    suspected_breakpoint: str
    severity: str
    request_count: int
    share_pct: float
    narrative: str
    operator_focus: str
    evidence_codes: List[str]
    representative_requests: List[InputHumanoidLoopHotspotRequestItem]
    recommended_view_path: str
    recommended_view_label: str


class InputHumanoidLoopHotspotsResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    status: str
    severity: str
    summary: str
    diagnosed_count: int
    hotspot_count: int
    hotspots: List[InputHumanoidLoopHotspotItem]
    timestamp: int


class InputHumanoidLoopHotspotTrendSeriesItem(BaseModel):
    organ_system: str
    organ_label: str
    request_counts: List[int]


class InputHumanoidLoopHotspotTrendBucketItem(BaseModel):
    bucket_index: int
    label: str
    sample_requests: int
    abnormal_requests: int
    dominant_organ_system: Optional[str]
    dominant_organ_label: Optional[str]
    dominant_root_cause_code: Optional[str]
    dominant_request_count: int
    dominant_share_pct: Optional[float]


class InputHumanoidLoopHotspotTrendResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    summary: str
    latest_dominant_organ_system: Optional[str]
    latest_dominant_organ_label: Optional[str]
    latest_dominant_root_cause_code: Optional[str]
    timeseries_labels: List[str]
    timeseries_total_requests: List[int]
    timeseries_abnormal_requests: List[int]
    dominant_buckets: List[InputHumanoidLoopHotspotTrendBucketItem]
    series: List[InputHumanoidLoopHotspotTrendSeriesItem]
    timestamp: int


class InputHumanoidLoopSourceCorrelationItem(BaseModel):
    source_id: str
    source_label: str
    source_kind: str
    confidence_pct: float
    impact_level: str
    source_status: Optional[str] = None
    correlated_hotspots: List[str]
    evidence: List[str]
    narrative: str
    operator_focus: str
    recommended_view_path: str
    recommended_view_label: str


class InputHumanoidLoopSourceCorrelationResponse(BaseModel):
    window_minutes: int
    interval_minutes: int
    status: str
    severity: str
    summary: str
    primary_source_system: Optional[str]
    primary_source_label: Optional[str]
    source_count: int
    items: List[InputHumanoidLoopSourceCorrelationItem]
    timestamp: int



__all__ = [name for name in globals() if name.startswith('InputHumanoidLoop')]
