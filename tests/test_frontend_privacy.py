"""Tests del aviso de privacidad (formatters + acción de bienvenida)."""
import ui.app as app
import ui.formatters as fmt


def test_welcome_points_to_the_privacy_notice() -> None:
    text = fmt.format_welcome(is_mock=False)

    assert "Privacidad" in text


def test_privacy_notice_covers_the_data_flow() -> None:
    text = fmt.format_privacy_notice()

    # Qué se procesa, con qué se identifica, dónde se guarda, qué proveedor
    # interviene, entrenamiento, cesiones y derechos del interesado.
    for expected in (
        "Google",
        "Groq",
        "entrenar modelos",
        "terceros",
        "fines comerciales",
        "eliminación",
        "conversaciones",
    ):
        assert expected in text, expected


def test_privacy_notice_is_plain_language() -> None:
    lowered = fmt.format_privacy_notice().lower()
    for token in ("token", "chroma", "llm", "api", "sqlite", "endpoint", "sql"):
        assert token not in lowered, token


def test_privacy_action_is_exposed_on_the_welcome() -> None:
    action = app._privacy_action()

    assert action.name == "aviso_privacidad"
    assert "Privacidad" in action.label
    assert action.tooltip
