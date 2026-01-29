"""
Modelo de Configuración - Gestiona la configuración del descargador.
La configuración se almacena en el navegador del usuario (localStorage).
"""

import logging

logger = logging.getLogger(__name__)

# Configuración por defecto para nuevos usuarios
DEFAULT_CONFIG = {
    "Client_ID": "f8068cf75621448184edc11474e60436",
    "Secret_ID": "45415e7db4bc4068b6bcc926ff300a6f",
    "Calidad_audio_video": "avg",
    "Formato_audio": "mp3",
    "Formato_video": "mp4",
    "Descargar_video": False,
    "Descargar_audio": True,
    "Fuente_descarga": "YouTube",  # "YouTube" o "Spotify"
    "Scrappear_metadata": True,
    "Mostrar_tiempo_de_ejecucion": True,
    "SponsorBlock_enabled": False,
    "SponsorBlock_categories": ["sponsor", "intro", "outro"],
    "Preferir_YouTube_Music": False,
}


class ModelFile:
    """Modelo para gestionar la configuración del descargador."""

    @classmethod
    def validate_config(cls, config_dict):
        """
        Valida y sanitiza una configuración.

        Args:
            config_dict: Diccionario con la configuración

        Returns:
            dict: Configuración validada y sanitizada
        """
        validated = DEFAULT_CONFIG.copy()

        if config_dict.get("Calidad_audio_video") in ["min", "avg", "max"]:
            validated["Calidad_audio_video"] = config_dict["Calidad_audio_video"]

        if config_dict.get("Formato_audio") in ["mp3", "wav", "m4a", "flac"]:
            validated["Formato_audio"] = config_dict["Formato_audio"]

        if config_dict.get("Formato_video") in ["mp4", "mov", "avi", "flv"]:
            validated["Formato_video"] = config_dict["Formato_video"]

        # Campos booleanos
        bool_fields = [
            "Descargar_video",
            "Descargar_audio",
            "Scrappear_metadata",
            "Mostrar_tiempo_de_ejecucion",
            "SponsorBlock_enabled",
            "Preferir_YouTube_Music",
        ]

        for field in bool_fields:
            if isinstance(config_dict.get(field), bool):
                validated[field] = config_dict[field]

        # Validar Fuente_descarga
        if config_dict.get("Fuente_descarga") in ["YouTube", "Spotify"]:
            validated["Fuente_descarga"] = config_dict["Fuente_descarga"]

        # Validar categorías de SponsorBlock
        valid_categories = [
            "sponsor",
            "intro",
            "outro",
            "selfpromo",
            "preview",
            "filler",
            "interaction",
            "music_offtopic",
        ]
        if isinstance(config_dict.get("SponsorBlock_categories"), list):
            validated["SponsorBlock_categories"] = [
                cat
                for cat in config_dict["SponsorBlock_categories"]
                if cat in valid_categories
            ]

        return validated
