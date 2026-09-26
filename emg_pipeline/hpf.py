"""Read the small XML metadata blocks in EMGworks HPF files.

Signal samples are decoded by the official Delsys File Utility, not here.
"""

from pathlib import Path
import re
import xml.etree.ElementTree as ET


def _xml_block(header: bytes, tag: str) -> ET.Element:
    match = re.search(
        rb"<" + tag.encode() + rb">.*?</" + tag.encode() + rb">",
        header,
        re.DOTALL,
    )
    if match is None:
        raise ValueError(f"HPF header is missing {tag}; unsupported or invalid HPF file")
    try:
        return ET.fromstring(match.group())
    except ET.ParseError as exc:
        raise ValueError(f"HPF {tag} metadata is malformed") from exc


def read_hpf_metadata(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"HPF file not found: {path}")
    with path.open("rb") as source:
        header = source.read(256 * 1024)
    recording = _xml_block(header, "HeaderInfoV1")
    channel_info = _xml_block(header, "ChannelInformationData")

    recording_date = recording.findtext("RecordingDate")
    if not recording_date:
        raise ValueError("HPF header has no RecordingDate")

    channels = []
    for item in channel_info.findall("ChannelInformation"):
        name = item.findtext("Name")
        unit = item.findtext("Unit")
        rate_text = item.findtext("PerChannelSampleRate")
        if not name or not unit or not rate_text:
            raise ValueError("HPF channel is missing name, unit, or sampling rate")
        try:
            rate = float(rate_text)
        except ValueError as exc:
            raise ValueError(f"Invalid sampling rate for {name}: {rate_text}") from exc
        if not 0 < rate < float("inf"):
            raise ValueError(f"Invalid sampling rate for {name}: {rate_text}")
        channels.append(
            {
                "name": name,
                "unit": unit,
                "sampling_rate_hz": rate,
                "start_time_in_hpf": item.findtext("StartTime"),
            }
        )
    if not channels or len({item["name"] for item in channels}) != len(channels):
        raise ValueError("HPF has no channels or has duplicate channel names")
    return {
        "recording_date_in_hpf": recording_date,
        "channel_count": len(channels),
        "channels": channels,
    }
