"""Description-only authoring retains frame coverage and legacy validation."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import final_report_lib as report
from analysis_tasks import output_skeleton
from analysis_handoff import check_material_part
from test_analysis_session_end_to_end import synthetic_inputs, authored_parts
from analysis_handoff import inspect_inputs
import test_final_report as fixtures


def observation():
    return {'frameRef': 'highlight:m-1:first_peak', 'interpretationStatus': 'observation_only',
            'visualObservation': '女性坐在桌前展示成排燕窝小罐，透明收纳盒内可见整齐排列的产品，画面叠加“四九折”优惠字幕。'}


def test_observation_only_round_trips_without_commercial_claim():
    value = observation()
    normalized = report._validate_v2_highlight_analysis(value, 0, 0)
    assert normalized['visualObservation'] == value['visualObservation']
    assert normalized['interpretationStatus'] == 'observation_only'
    assert not normalized.get('commercialInterpretation')
    assert report._validate_v2_highlight_analysis(normalized, 0, 0) == normalized


@pytest.mark.parametrize('extra', [
    {'commercialInterpretation': 'This frame drives conversion'},
    {'limitation': 'Not a requested inference'},
    {'sharedInterpretationRef': 'highlight:m-1:peak_top_2'},
    {'visualObservation': ''},
])
def test_observation_only_rejects_contradictory_fields_and_empty_description(extra):
    with pytest.raises(report.FinalReportError):
        report._validate_v2_highlight_analysis({**observation(), **extra}, 0, 0)


def test_material_skeleton_accepts_description_without_forcing_commercial_text():
    value = output_skeleton('material-001', {'materialId': 'm-1',
        'highlight': {'frames': [{'evidenceRef': 'highlight:m-1:first_peak'}]}}, 'fixture')
    frame = value['materialAnalyses'][0]['highlightAnalyses'][0]
    frame['visualObservation'] = observation()['visualObservation']
    normalized = report._validate_v2_highlight_analysis(frame, 0, 0)
    assert normalized['interpretationStatus'] == 'observation_only'
    assert 'commercialInterpretation' not in frame


def test_description_only_report_retains_text():
    helper = fixtures.FinalReportV4HighlightDashboardTests()
    helper.setUp()
    value = helper._conclusion()
    for material in value['materialAnalyses']:
        for frame in material['highlightAnalyses']:
            frame.pop('commercialInterpretation')
            frame['interpretationStatus'] = 'observation_only'
    data = helper._build_data(value)
    assert data['materials'][0]['analysis']['highlightAnalyses'][0]['interpretationStatus'] == 'observation_only'
    assert data['materials'][0]['analysis']['highlightAnalyses'][0]['visualObservation'] == value['materialAnalyses'][0]['highlightAnalyses'][0]['visualObservation']


def test_material_precheck_does_not_waive_missing_or_foreign_frames(tmp_path):
    root = synthetic_inputs(tmp_path)
    source = authored_parts(inspect_inputs(root)['manifestSha256'])[3][1]
    for frame in source['materialAnalyses'][0]['highlightAnalyses']:
        frame.pop('limitation')
        frame['interpretationStatus'] = 'observation_only'
    path = root / 'part.json'
    path.write_text('{}')
    result = check_material_part(root, path, _part=source)
    assert result['status'] == 'material_structure_checked'
    for mode in ('missing', 'foreign'):
        bad = copy.deepcopy(source)
        frames = bad['materialAnalyses'][0]['highlightAnalyses']
        if mode == 'missing':
            frames.pop()
        else:
            frames[0]['frameRef'] = 'highlight:foreign:first_peak'
        with pytest.raises(ValueError, match='every successful frame'):
            check_material_part(root, path, _part=bad)


def test_empty_observation_error_does_not_request_a_commercial_interpretation():
    with pytest.raises(report.FinalReportError) as error:
        report._validate_v2_highlight_analysis({**observation(), 'visualObservation': ''}, 0, 0)
    assert 'visualObservation' in str(error.value)
    assert 'commercialInterpretation' not in str(error.value)
