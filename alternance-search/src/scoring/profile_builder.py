"""
Profile Builder — LLM-powered candidate profile construction from chat + documents.

The LLM helps build a condensed Markdown profile from user messages, dragged documents
(CV, cover letter, notes, etc.). The profile is a simple .md text that the user can also
edit manually in a textarea.

Expected final profile format (Markdown):
# Profil Candidat

## Poste recherché
Data Scientist

## Compétences techniques
- Python
- SQL
- Machine Learning

## Niveau d'études
BAC+5

## Formation
- Master Data Science, Université Paris, 2024

## Expérience
- Stage Data Analyst, Entreprise X, 6 mois

## Localisation souhaitée
Île-de-France

## Contrat
Alternance

## Langues
- Français (natif)
- Anglais (B2)

## Soft skills
- Travail d'équipe
- Autonomie

## Résumé
Profil orienté data science avec une solide formation...
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
    attachments: list[dict] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ProfileSession:
    """Holds the full conversation + accumulated materials for one profile build."""
    session_id: str
    messages: list[ChatMessage] = field(default_factory=list)
    collected_materials: list[dict] = field(default_factory=list)
    current_profile_md: str = ""  # Markdown string
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ═══════════════════════════════════════════════════════════════════════
# Prompt templates
# ═══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """Tu es un assistant spécialisé dans la construction de profils candidats pour la recherche d'alternance.

Ton rôle : à partir des documents, messages et informations que l'utilisateur te fournit (CV, lettres de motivation, notes, descriptions de compétences...), tu construis progressivement un profil candidat structuré en **Markdown**.

Règles :
- Analyse chaque document/message reçu et extrait les informations pertinentes
- Si le profil existe déjà, fusionne les nouvelles informations sans perdre les anciennes
- Pose des questions si des champs importants sont manquants
- Le profil final doit être en français
- Sois concis dans tes réponses (2-4 phrases max)

Format du profil à construire (Markdown obligatoire) :
```md
# Profil Candidat

## Poste recherché
Intitulé du poste

## Compétences techniques
- Compétence 1
- Compétence 2

## Niveau d'études
BAC+3 / BAC+5...

## Formation
- Diplôme, École, Année

## Expérience
- Poste, Entreprise, Durée

## Localisation souhaitée
Ville ou région

## Contrat
Alternance / Stage / CDI...

## Langues
- Français (natif)
- Anglais (B2)

## Soft skills
- Travail d'équipe
- Autonomie

## Résumé
Résumé du profil en 2-3 phrases
```

Quand tu réponds :
- Commence par un bref accusé de réception de ce que tu as compris
- Termine TOUJOURS ton message par le profil Markdown actuel entre ```md et ```
- Si le profil est vide, mets un template avec des champs vides
- Ne répète pas tout le profil s'il n'a pas changé, dis juste "Profil inchangé" et mets le Markdown

Exemple de réponse :
"J'ai bien pris en compte ton CV. Tu es en Licence Informatique à l'Université de Paris, avec des compétences en Python et SQL. Je vois que tu cherches une alternance en Data Science. As-tu une préférence de localisation ?

```md
# Profil Candidat

## Poste recherché
Data Scientist

## Compétences techniques
- Python
- SQL
- Machine Learning

## Niveau d'études
BAC+3

...
```"
"""

BUILD_PROFILE_PROMPT = """Finalise le profil candidat à partir de tous les éléments collectés ci-dessous.
Retourne UNIQUEMENT le Markdown du profil, sans commentaire ni autre texte."""


GENERATE_TERMS_PROMPT = """Tu es un assistant spécialisé dans le marché de l'emploi tech en France.

À partir du profil candidat ci-dessous, génère une liste de 5 à 10 termes de recherche pertinents pour trouver des offres d'alternance/emploi.

Règles :
- Chaque terme doit être un intitulé de poste, un domaine ou une technologie
- en français ou anglais selon le plus pertinent
- Variés : un terme large, un spécialisé, un par technologie clé, un par secteur
- Retourne UNIQUEMENT un JSON array de strings, sans commentaire
- Exemple : ["Data Scientist", "Data Analyst", "Machine Learning Engineer", "Python Developer", "Analyste Data", "IA"]

Profil :
{profile_md}"""


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
        current_profile_md: str = "",
        history: list[dict] | None = None,
    ) -> dict:
        """
        Send a message to the LLM to build/update the markdown profile.

        Returns:
            {
                "reply": str,              # LLM's natural language response
                "profile_md": str,         # extracted/updated profile in Markdown
                "profile_changed": bool
            }
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Inject current profile as context
        if current_profile_md and current_profile_md.strip():
            profile_context = (
                "Profil candidat actuel :\n"
                + current_profile_md
            )
            messages.append({"role": "system", "content": profile_context})

        # Add conversation history
        if history:
            for h in history[-10:]:
                role = h.get("role", "user")
                content = h.get("content", "")
                if role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})

        # Build the user message with attachments
        user_content = message
        if attachment_texts:
            for i, text in enumerate(attachment_texts):
                preview = text[:3000]
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
                "profile_md": current_profile_md or "",
                "profile_changed": False,
                "error": str(e),
            }

        # Extract profile Markdown from the reply
        new_profile_md = self._extract_profile_md(reply)

        profile_changed = (new_profile_md is not None and new_profile_md.strip() != (current_profile_md or "").strip())

        return {
            "reply": reply,
            "profile_md": new_profile_md if new_profile_md else (current_profile_md or ""),
            "profile_changed": profile_changed,
        }

    def build_final_profile(self, materials: list[str], partial_profile_md: str = "") -> dict:
        """Build a final markdown profile from all collected materials."""
        combined = "\n\n---\n\n".join(
            f"Document {i+1}:\n{m[:5000]}" for i, m in enumerate(materials)
        )

        prompt = (
            BUILD_PROFILE_PROMPT
            + "\n\n"
            + combined
            + "\n\nProfil partiel actuel :\n"
            + (partial_profile_md or "(vide)")
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
            return {"profile_md": partial_profile_md or "", "error": str(e)}

        profile_md = self._extract_profile_md(reply)
        return {"profile_md": profile_md or partial_profile_md or ""}

    # ── Helpers ─────────────────────────────────────────────────────────

    def generate_search_terms(self, profile_md: str) -> list[str]:
        """Génère des termes de recherche à partir du profil Markdown."""
        if not profile_md or not profile_md.strip():
            return []

        prompt = GENERATE_TERMS_PROMPT.replace("{profile_md}", profile_md)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=512,
            )
            reply = response.choices[0].message.content or ""
        except Exception:
            return []

        # Extract JSON array from reply
        try:
            terms = json.loads(reply)
            if isinstance(terms, list):
                return [str(t).strip() for t in terms if str(t).strip()]
        except json.JSONDecodeError:
            pass

        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", reply, re.DOTALL)
        if match:
            try:
                terms = json.loads(match.group(1))
                if isinstance(terms, list):
                    return [str(t).strip() for t in terms if str(t).strip()]
            except json.JSONDecodeError:
                pass

        match = re.search(r'\[(.*?)\]', reply, re.DOTALL)
        if match:
            try:
                terms = json.loads(f'[{match.group(1)}]')
                if isinstance(terms, list):
                    return [str(t).strip() for t in terms if str(t).strip()]
            except (json.JSONDecodeError, ValueError):
                pass

        return []

    def _extract_profile_md(self, text: str) -> str | None:
        """Extract the Markdown profile block from the LLM reply."""
        # Try ```md ... ``` block
        match = re.search(r"```md\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try ```markdown ... ``` block
        match = re.search(r"```markdown\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try generic ``` ... ``` block that starts with #
        match = re.search(r"```\s*\n?(# .*?)\n?\s*```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Fallback: find everything from "# Profil" or "# " to end of text
        # (LLM might just write raw markdown without code block)
        match = re.search(r"(# (?:Profil|Profile).*)$", text, re.DOTALL)
        if match:
            return match.group(1).strip()

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
