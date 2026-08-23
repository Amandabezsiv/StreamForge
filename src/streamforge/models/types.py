from enum import StrEnum


class VideoStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"


class JobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobType(StrEnum):
    PROCESS_VIDEO = "PROCESS_VIDEO"


class OutputType(StrEnum):
    THUMBNAIL = "THUMBNAIL"
    TRANSCODED_VIDEO = "TRANSCODED_VIDEO"
