"""Human-friendly Error Engine & Painel Raio-X with stable error codes (E-FFMPEG-001, E-OLLAMA-002...),
probable cause, actionable fix instructions in Portuguese, exact file/line, and AI-ready diagnostics."""

from __future__ import annotations

import inspect
import traceback
from datetime import datetime, timezone
from typing import Any, Optional

_ERROR_CATALOG: dict[str, tuple[str, str, str]] = {
    "FFMPEG": (
        "E-FFMPEG-001",
        "Executavel FFmpeg ausente, corrompido ou filtro/video incompativel.",
        "Confira o caminho do ffmpeg.exe em Configuracoes e verifique se o MP4 de origem abre normalmente.",
    ),
    "OLLAMA": (
        "E-OLLAMA-002",
        "Servico Ollama offline em http://localhost:11434 ou modelo Qwen 3.5 nao carregado.",
        "Clique no indicador do Qwen no topo para iniciar o Ollama ou rode 'ollama pull qwen3.5:9b'.",
    ),
    "UPLOAD": (
        "E-UPLOAD-003",
        "Arquivo de video nao encontrado, formato nao suportado ou disco sem espaco.",
        "Use arquivos MP4/MKV/MOV/AVI/WEBM validos e verifique o espaco livre na aba Limpeza de Disco.",
    ),
    "RENDER": (
        "E-RENDER-004",
        "Falha ao codificar ou filtrar o clipe final no FFmpeg.",
        "Tente trocar o Encoder para libx264 em Configuracoes ou reduza efeitos simultaneos.",
    ),
    "ANALISE": (
        "E-ANALISE-005",
        "Falha ao extrair audio ou frames durante a analise multimodal da gameplay.",
        "Verifique se o video possui trilha valida e nao foi movido/aberto exclusivamente por outro programa.",
    ),
    "YOUTUBE_AUTH": (
        "E-YT-AUTH-006",
        "Credenciais client_secrets.json invalidas ou sessao OAuth2 do Google expirada.",
        "Reimporte o client_secrets.json (OAuth Desktop App) e clique em Conectar Conta YouTube.",
    ),
    "YOUTUBE_UPLOAD": (
        "E-YT-UP-007",
        "Erro na YouTube Data API v3 ou cota diaria excedida.",
        "Verifique sua cota no Google Cloud Console e tente novamente mais tarde.",
    ),
    "YOUTUBE_PROTECTION": (
        "E-YT-SAFE-008",
        "Regra de protecao de conta bloqueou envio automatico nao aprovado ou acima do limite diario.",
        "Aprove explicitamente o video na fila do Agendador ou ajuste o limite diario de uploads.",
    ),
    "SEGURANCA": (
        "E-SEC-009",
        "Requisicao bloqueada pela blindagem local (Host, Origin, X-ClipRush-Token ou Path Traversal).",
        "Recarregue a interface em http://127.0.0.1:8765 para renovar o token da sessao local.",
    ),
    "WATCHER": (
        "E-WATCH-010",
        "Pasta monitorada do PS4 nao esta acessivel ou o pendrive/HD externo foi desconectado.",
        "Conecte o dispositivo (ex: E:\\PS4\\SHARE\\Video Clips) ou configure outro caminho.",
    ),
}


def resolve_error_metadata(category: str) -> tuple[str, str, str]:
    cat = (category or "SISTEMA").upper()
    return _ERROR_CATALOG.get(
        cat,
        (
            "E-SYS-099",
            "Erro interno inesperado durante a operacao solicitada.",
            "Copie os detalhes abaixo e abra os logs para diagnostico rapido.",
        ),
    )


class XRayError(Exception):
    """Application error carrying stable code, human PT-BR cause/fix, and exact source location."""

    def __init__(
        self,
        message_pt: str,
        category: str = "SISTEMA",
        technical_detail: str = "",
        status_code: int = 500,
        source_file: Optional[str] = None,
        source_line: Optional[int] = None,
        context: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
        probable_cause: Optional[str] = None,
        how_to_fix: Optional[str] = None,
    ) -> None:
        super().__init__(message_pt)
        frame = inspect.currentframe()
        caller = frame.f_back if frame else None
        self.message_pt = message_pt
        self.category = category
        self.technical_detail = technical_detail or message_pt
        self.status_code = status_code
        self.source_file = source_file or (caller.f_code.co_filename if caller else "unknown")
        self.source_line = source_line or (caller.f_lineno if caller else 0)
        self.context = context or {}
        self.timestamp = datetime.now(timezone.utc).isoformat()
        cat_code, cat_cause, cat_fix = resolve_error_metadata(category)
        self.error_code = error_code or cat_code
        self.probable_cause = probable_cause or cat_cause
        self.how_to_fix = how_to_fix or cat_fix

    def to_payload(self, tb_text: str = "") -> dict[str, Any]:
        ai_prompt = (
            "=== CLIPRUSH STUDIO PRO - RELATORIO RAIO-X DE ERRO ===\n"
            f"Codigo: {self.error_code}\n"
            f"Data/Hora (UTC): {self.timestamp}\n"
            f"Categoria: {self.category}\n"
            f"Explicacao: {self.message_pt}\n"
            f"Causa Provavel: {self.probable_cause}\n"
            f"Como Resolver: {self.how_to_fix}\n"
            f"Arquivo: {self.source_file}\n"
            f"Linha: {self.source_line}\n"
            f"Detalhe Tecnico: {self.technical_detail}\n"
        )
        if self.context:
            ai_prompt += f"Contexto: {self.context}\n"
        if tb_text:
            ai_prompt += f"Traceback:\n{tb_text.strip()}\n"
        ai_prompt += "======================================================"
        return {
            "error": True,
            "code": self.error_code,
            "category": self.category,
            "explanation_pt": self.message_pt,
            "probable_cause_pt": self.probable_cause,
            "how_to_fix_pt": self.how_to_fix,
            "technical_detail": self.technical_detail,
            "file": self.source_file,
            "line": self.source_line,
            "timestamp": self.timestamp,
            "context": self.context,
            "traceback": tb_text.strip(),
            "ai_copy_text": ai_prompt,
        }


def build_xray_from_exception(
    exc: Exception,
    category: str = "SISTEMA",
    friendly_pt: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Convert any unexpected Python exception into a standardized X-Ray payload."""
    if isinstance(exc, XRayError):
        return exc.to_payload(traceback.format_exc())

    tb = traceback.extract_tb(exc.__traceback__)
    src_file = "unknown"
    src_line = 0
    if tb:
        project_frames = [f for f in tb if "ClipRush_Pro_V2" in f.filename]
        chosen = project_frames[-1] if project_frames else tb[-1]
        src_file = chosen.filename
        src_line = chosen.lineno or 0

    default_pt = friendly_pt or _infer_portuguese_explanation(exc, category)
    xray = XRayError(
        message_pt=default_pt,
        category=category,
        technical_detail=f"{type(exc).__name__}: {exc}",
        source_file=src_file,
        source_line=src_line,
        context=context,
    )
    return xray.to_payload(traceback.format_exc())


def _infer_portuguese_explanation(exc: Exception, category: str) -> str:
    text = str(exc).lower()
    if "ffmpeg" in text or "ffprobe" in text or category == "FFMPEG":
        return (
            "Falha ao executar o FFmpeg no processamento de video. "
            "Verifique se o arquivo de video nao esta corrompido e se o caminho do FFmpeg nas Configuracoes esta valido."
        )
    if "ollama" in text or "11434" in text or category == "OLLAMA":
        return (
            "Nao foi possivel se comunicar com o Ollama (Qwen 3.5 9B). "
            "Verifique se o Ollama esta aberto em http://localhost:11434 e se o modelo qwen3.5:9b esta instalado."
        )
    if isinstance(exc, FileNotFoundError):
        return "Arquivo ou pasta nao encontrado no disco. Verifique se o video original nao foi movido ou renomeado."
    if isinstance(exc, PermissionError):
        return "Permissao negada ao ler ou salvar arquivo no disco. Feche outros programas que estejam usando esse arquivo."
    if category == "UPLOAD":
        return "Erro ao importar ou enviar o arquivo de gameplay. Confira o espaco em disco e o formato do video (MP4/MKV/MOV)."
    if category == "RENDER":
        return "Erro durante a renderizacao do clipe. Tente novamente ou verifique os logs tecnicos abaixo."
    return f"Ocorreu um erro inesperado no modulo {category}: {type(exc).__name__}."
