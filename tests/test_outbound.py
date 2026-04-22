"""Tests for OneBot outbound helpers."""

from __future__ import annotations

from nanobot_channel_anon.outbound import (
    get_suppressed_outbound_reason,
    split_outbound_batches,
)


def test_get_suppressed_outbound_reason_matches_known_fallbacks() -> None:
    """Known nanobot fallback texts should be suppressed."""
    assert get_suppressed_outbound_reason(
        "I completed the tool steps but couldn't produce a final answer. "
        "Please try again or narrow the task."
    ) == "empty_final_response"
    assert (
        get_suppressed_outbound_reason(
            "Sorry, I encountered an error calling the AI model."
        )
        == "model_error"
    )
    assert (
        get_suppressed_outbound_reason("Sorry, I encountered an error.")
        == "generic_error"
    )
    assert (
        get_suppressed_outbound_reason(
            "Task completed but no final response was generated."
        )
        == "missing_final_response"
    )
    assert (
        get_suppressed_outbound_reason("Background task completed.")
        == "background_task_completed"
    )


def test_get_suppressed_outbound_reason_matches_max_iterations() -> None:
    """Max-iterations fallback should be suppressed."""
    assert (
        get_suppressed_outbound_reason(
            "I reached the maximum number of tool call iterations (15) without "
            "completing the task. You can try breaking the task into smaller steps."
        )
        == "max_iterations"
    )


def test_get_suppressed_outbound_reason_matches_error_prefix() -> None:
    """All Error:-prefixed texts should be suppressed."""
    assert (
        get_suppressed_outbound_reason(
            " Error: Task interrupted before this tool finished. "
        )
        == "error_prefix"
    )
    assert (
        get_suppressed_outbound_reason("Error: Message sending not configured")
        == "error_prefix"
    )


def test_get_suppressed_outbound_reason_preserves_allowed_text() -> None:
    """Normal replies and slash-command responses should not be suppressed."""
    assert get_suppressed_outbound_reason("") is None
    assert get_suppressed_outbound_reason("   ") is None
    assert get_suppressed_outbound_reason(
        "The upstream returned error code 500."
    ) is None
    assert get_suppressed_outbound_reason("Restarting...") is None
    assert get_suppressed_outbound_reason("New session started.") is None
    assert get_suppressed_outbound_reason("Stopped 1 task(s).") is None
    assert get_suppressed_outbound_reason("No active task to stop.") is None
    assert get_suppressed_outbound_reason("Dreaming...") is None


def test_split_outbound_batches_keeps_images_with_caption() -> None:
    """Images may still be sent together with caption text."""
    assert split_outbound_batches(
        "caption",
        ["/napcat/a.png", "/napcat/b.jpg"],
    ) == [("caption", ["/napcat/a.png", "/napcat/b.jpg"])]


def test_split_outbound_batches_splits_non_image_media() -> None:
    """Voice, video, and files should be sent separately from text."""
    assert split_outbound_batches(
        "caption",
        ["/napcat/a.wav", "/napcat/b.mp4", "/napcat/c.zip"],
    ) == [
        ("", ["/napcat/a.wav"]),
        ("", ["/napcat/b.mp4"]),
        ("", ["/napcat/c.zip"]),
        ("caption", []),
    ]


def test_split_outbound_batches_preserves_media_order_around_images() -> None:
    """Mixed media should preserve original order while keeping caption legal."""
    assert split_outbound_batches(
        "caption",
        ["/napcat/a.png", "/napcat/b.wav", "/napcat/c.jpg"],
    ) == [
        ("", ["/napcat/a.png"]),
        ("", ["/napcat/b.wav"]),
        ("caption", ["/napcat/c.jpg"]),
    ]
