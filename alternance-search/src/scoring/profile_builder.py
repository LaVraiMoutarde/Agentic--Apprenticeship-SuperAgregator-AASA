"""
Profile Builder — LLM-powered candidate profile construction from chat + documents.

This module handles the LLM conversation that progressively builds a structured
candidate profile (JSON) from user messages, dragged documents, and snippets.

The LLM is prompted to:
  1. Extract relevant info from each dropped item (CV, cover letter, notes, etc.)
  2. Maintain and refine a structured profile incrementally
  3. Finalize the profile on demand

Expected final profile format:
  {
    "desired_role": "...",
    "skills": ["...", "..."],
    "education_level": "...",
    "education_details": [{"degree": "...", "school": "...", "year": "..."}],
    "experience": [{"title": "...", "company": "...", "duration": "...", "description": "..."}],
    "preferred_location": "...",
    "preferred_contract": "...",
    "languages": ["..."],
    "soft_skills": ["..."],
    "summary": "..."
  }
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from openai import OpenAI


# ═══════════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ChatMessage:
    """A single message in the profile builder conversation."""
    role: str  # "user" | "assistant" | "system"
    content: str
    attachments: list[dict] = field(default_factory=list)  # [{name, type, preview}]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ProfileSession:
    """Holds the full conversation + accumulated materials for one profile build."""
    session_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    collected_materials: list[dict] = field(default_factory=list)  # raw dropped contents
    current_profile: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ═══════════════════════════════════════════════════════════════════════
# Prompt templates
# ═══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Tu es un assistant spécialisé dans la construction de profils candidats pour la recherche d'alternance.

Ton rôle : à partir des documents, messages et informations que l'utilisateur te fournit (CV, lettres de motivation, notes, descriptions de compétences...), tu construis progressivement un profil candidat structuré en JSON.

Règles :
- Analyse chaque document/message reçu et extrait les informations pertinentes
- Si le profil existe déjà, fusionne les nouvelles informations sans perdre les anciennes
- Pose des questions si des champs importants sont manquants
- Le profil final doit être en français
- Sois concis dans tes réponses (2-4 phrases max)

Format du profil à construire :
```json
{
  "desired_role": "Intitulé du poste recherché",
  "skills": ["Compétence 1", "Compétence 2"],
  "education_level": "BAC+3 / BAC+5...",
  "education_details": [
    {"degree": "Licence Informatique", "school": "Université X", "year": "2024"}
  ],
  "experience": [
    {"title": "Stage Développeur", "company": "Entreprise Y", "duration": "6 mois", "description": "..."}
  ],
  "preferred_location": "Île-de-France",
  "preferred_contract": "Alternance / Apprentissage",
  "languages": ["Français (natif)", "Anglais (B2)"],
  "soft_skills": ["Travail d'équipe", "Autonomie"],
  "summary": "Résumé du profil en 2-3 phrases"
}
```

Quand tu réponds :
- Commence par un bref accusé de réception de ce que tu as compris
- Termine TOUJOURS ton message par le profil JSON actuel entre ```json et ```
- Si le profil est vide, mets un JSON avec des chaînes vides
- Ne répète pas tout le profil s'il n'a pas changé, dis juste "Profil inchangé" et mets le JSON

Exemple de réponse :
"J'ai bien pris en compte ton CV. Tu es en Licence Informatique à l'Université de Paris, avec des compétences en Python et SQL. Je vois que tu cherches une alternance en Data Science. As-tu une préférence de localisation ?

```json
{
  "desired_role": "Data Scientist",
  "skills": ["Python", "SQL", "Machine Learning"],
  "education_level": "BAC+3",
  ...
}
```"
"""

BUILD_PROFILE_PROMPT = """Finalise le profil candidat à partir de tous les éléments collectés ci-dessous.
Retourne UNIQUEMENT le JSON du profil, sans commentaire ni markdown."""


GENERATE_TERMS_PROMPT = """Tu es un assistant spécialisé dans le marché de l'emploi tech en France.

À partir du profil candidat ci-dessous, génère une liste de 5 à 10 termes de recherche pertinents pour trouver des offres d'alternance/emploi.

Règles :
- Chaque terme doit être un intitulé de poste, un domaine ou une technologie
- en français ou anglais selon le plus pertinent
- Variés : un terme large, un spécialisé, un par technologie clé, un par secteur
- Retourne UNIQUEMENT un JSON array de strings, sans commentaire
- Exemple : ["Data Scientist", "Data Analyst", "Machine Learning Engineer", "Python Developer", "Analyste Data", "IA"]

Profil :
```json
{profile_json}
```"""


# ═══════════════════════════════════════════════════════════════════════
# ProfileBuilder class
# ═══════════════════════════════════════════════════════════════════════

class ProfileBuilder:
    """Manages an LLM-powered conversation to build a candidate profile."""

    def __init__(
        self,
        provider: str = "ollama",
        model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "",
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self.client = OpenAI(
            base_url=base_url if base_url else "http://localhost:11434/v1",
            api_key=api_key if api_key else "ollama",
        )

    # ── Public API ─────────────────────────────────────────────────────

    def chat(
        self,
        message: str,
        attachment_texts: list[str] | None = None,
        current_profile: dict | None = None,
        history: list[dict] | None = None,
    ) -> dict:
        """
        Send a message (and optional attached file contents) to the LLM.

        Returns:
            {
                "reply": str,          # LLM's natural language response
                "profile": dict,       # extracted/updated profile JSON
                "profile_changed": bool
            }
        """
        # Build the messages list
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Inject current profile as context if it exists
        if current_profile and any(
            v for v in current_profile.values() if v
        ):
            profile_context = (
                "Profil candidat actuel :\n```json\n"
                + json.dumps(current_profile, ensure_ascii=False, indent=2)
                + "\n```"
            )
            messages.append({"role": "system", "content": profile_context})

        # Add conversation history
        if history:
            for h in history[-10:]:  # last 10 messages for context
                role = h.get("role", "user")
                content = h.get("content", "")
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})

        # Build the user message with attachments
        user_content = message
        if attachment_texts:
            for i, text in enumerate(attachment_texts):
                preview = text[:3000]  # truncate to save tokens
                user_content += f"\n\n--- Document {i+1} ---\n{preview}"

        messages.append({"role": "user", "content": user_content})

        # Call LLM
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.3,
                max_tokens=2048,
            )
            reply = response.choices[0].message.content or ""
        except Exception as e:
            return {
                "reply": f"Erreur LLM : {e}",
                "profile": current_profile or {},
                "profile_changed": False,
                "error": str(e),
            }

        # Extract profile JSON from the reply
        new_profile = self._extract_profile_json(reply)

        # Determine if profile changed
        profile_changed = False
        if new_profile and current_profile:
            profile_changed = json.dumps(new_profile, sort_keys=True) != json.dumps(
                current_profile, sort_keys=True
            )
        elif new_profile:
            profile_changed = True

        return {
            "reply": reply,
            "profile": new_profile or current_profile or {},
            "profile_changed": profile_changed,
        }

    def build_final_profile(self, materials: list[str], partial_profile: dict | None = None) -> dict:
        """
        Build a final profile from all collected materials in one shot.
        Useful for "finalize" action.
        """
        combined = "\n\n---\n\n".join(
            f"Document {i+1}:\n{m[:5000]}" for i, m in enumerate(materials)
        )

        prompt = (
            BUILD_PROFILE_PROMPT
            + "\n\n"
            + combined
            + "\n\nProfil partiel actuel :\n```json\n"
            + json.dumps(partial_profile or {}, ensure_ascii=False, indent=2)
            + "\n```"
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.0,
                max_tokens=2048,
            )
            reply = response.choices[0].message.content or ""
        except Exception as e:
            return {
                "profile": partial_profile or {},
                "error": str(e),
            }

        profile = self._extract_profile_json(reply)
        return {"profile": profile or partial_profile or {}}

    # ── Helpers ─────────────────────────────────────────────────────────

    def generate_search_terms(self, profile: dict) -> list[str]:
        """Génère une liste de termes de recherche à partir d'un profil candidat.

        Args:
            profile: Dictionnaire du profil (format chat builder).

        Returns:
            Liste de termes de recherche (strings).
        """
        if not profile or not any(v for v in profile.values() if v):
            return []

        prompt = GENERATE_TERMS_PROMPT.replace(
            "{profile_json}",
            json.dumps(profile, ensure_ascii=False, indent=2),
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=512,
            )
            reply = response.choices[0].message.content or ""
        except Exception as e:
            return []

        # Extract JSON array from reply
        try:
            # Try parsing full reply as JSON
            terms = json.loads(reply)
            if isinstance(terms, list):
                return [str(t).strip() for t in terms if str(t).strip()]
        except json.JSONDecodeError:
            pass

        # Try extracting ```json ... ``` block
        import re
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", reply, re.DOTALL)
        if match:
            try:
                terms = json.loads(match.group(1))
                if isinstance(terms, list):
                    return [str(t).strip() for t in terms if str(t).strip()]
            except json.JSONDecodeError:
                pass

        # Fallback: try to find [...] in the text
        match = re.search(r'\[(.*?)\]', reply, re.DOTALL)
        if match:
            try:
                terms = json.loads(f'[{match.group(1)}]')
                if isinstance(terms, list):
                    return [str(t).strip() for t in terms if str(t).strip()]
            except (json.JSONDecodeError, ValueError):
                pass

        return []

    def _extract_profile_json(self, text: str) -> dict | None:
        """Extract the JSON profile block from the LLM reply."""
        # Try ```json ... ``` block first
        match = re.search(r"```json\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try ``` ... ``` block
        match = re.search(r"```\s*\n(.*?)\n\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try raw JSON object anywhere
        match = re.search(r"\{[^{}]*\}", text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return None


# ═══════════════════════════════════════════════════════════════════════
# Simple text extractor for common file types
# ═══════════════════════════════════════════════════════════════════════

def extract_text_from_bytes(filename: str, content: bytes) -> str:
    """Extract text from uploaded file bytes based on extension."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""

    if ext in ("txt", "md", "py", "js", "html", "css", "json", "csv"):
        return content.decode("utf-8", errors="replace")

    if ext == "pdf":
        try:
            import io
            from PyPDF2 import PdfReader
            reader = PdfReader(io.BytesIO(content))
            return "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
        except ImportError:
            return "[PDF — PyPDF2 non installé]"
        except Exception as e:
            return f"[Erreur lecture PDF : {e}]"

    if ext in ("docx",):
        try:
            import io
            from docx import Document
            doc = Document(io.BytesIO(content))
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            return "[DOCX — python-docx non installé]"
        except Exception as e:
            return f"[Erreur lecture DOCX : {e}]"

    if ext in ("png", "jpg", "jpeg", "gif", "bmp", "webp"):
        return "[Image — conversion texte non supportée. Décrivez le contenu dans le chat.]"

    # Default: try UTF-8, fallback to binary description
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return f"[Fichier binaire {ext} — {len(content)} octets]"
