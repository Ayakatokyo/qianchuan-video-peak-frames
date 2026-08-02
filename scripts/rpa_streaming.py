"""Pure state helpers used by the streaming RPA scheduler."""


def stream_entry(material, rank, empty_timings):
    entry = dict(material)
    entry.update(
        {
            "rank": rank,
            "materialId": str(material.get("materialId") or "").strip(),
            "rpaStatus": "pending",
            "postprocessStatus": "pending",
            "status": "pending",
            "failureReason": None,
            "errorType": None,
            "errorMessage": None,
            "diagnosisRecordsPath": None,
            "diagnosisCsvPath": None,
            "recordsPersisted": False,
            "postprocessSucceeded": False,
            "rpaSoftTimeoutExceeded": False,
            "summaryJson": None,
            "summaryMd": None,
            "timingsSec": empty_timings(),
        }
    )
    return entry


def public_entry(entry):
    return {key: value for key, value in entry.items() if key != "records"}


def ordered(entries, public_entry_fn=public_entry):
    return [public_entry_fn(entries[index]) for index in sorted(entries)]


def accounted_indices(*entry_maps):
    accounted = set()
    for entry_map in entry_maps:
        accounted.update(entry_map)
    return accounted
