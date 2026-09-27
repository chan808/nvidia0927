"""Create the one-page repository-link PDF requested by the application form."""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer


ROOT = Path(__file__).resolve().parents[1]
FONT_PATH = Path(r"C:\Windows\Fonts\malgun.ttf")
REPO_URL = "https://github.com/chan808/nvidia0927"


def create_pdf(team_name: str, destination: Path | None = None) -> Path:
    if not team_name or not re.fullmatch(r"[\w가-힣 -]{1,40}", team_name):
        raise ValueError("Use the exact team name, without path separators or punctuation")
    if not FONT_PATH.exists():
        raise RuntimeError("Malgun Gothic font is required for Korean PDF generation on Windows")
    pdfmetrics.registerFont(TTFont("Malgun", str(FONT_PATH)))
    output = destination or ROOT / "output" / "pdf" / f"NVIDIA 해커톤_{team_name}_TraceBridge.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)

    navy = colors.HexColor("#14243A")
    teal = colors.HexColor("#087F8C")
    gray = colors.HexColor("#44546A")
    title = ParagraphStyle("title", fontName="Malgun", fontSize=23, leading=30, textColor=navy, spaceAfter=8)
    subtitle = ParagraphStyle("subtitle", fontName="Malgun", fontSize=10, leading=17, textColor=gray, wordWrap="CJK")
    heading = ParagraphStyle("heading", fontName="Malgun", fontSize=12, leading=20, textColor=teal, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", fontName="Malgun", fontSize=9.5, leading=16.5, textColor=navy, wordWrap="CJK")
    link = ParagraphStyle("link", parent=body, fontSize=10.5, leading=18, textColor=teal)

    story = [
        Paragraph("TraceBridge", title),
        Paragraph("NVIDIA KOREA Agentic AI Hackathon - 온라인 사전 챌린지", subtitle),
        Spacer(1, 12),
        HRFlowable(width="100%", thickness=1, color=teal),
        Spacer(1, 14),
        Paragraph("팀명", heading),
        Paragraph(escape(team_name), body),
        Paragraph("서비스 명", heading),
        Paragraph("TraceBridge - 오류 제보를 검증 가능한 재현 사례로 바꾸는 에이전트", body),
        Paragraph("프로젝트 저장소", heading),
        Paragraph(f'<link href="{REPO_URL}" color="#087F8C"><u>{REPO_URL}</u></link>', link),
        Paragraph("핵심 흐름", heading),
        Paragraph("프론트엔드 오류 제보를 한 trace ID의 실제 요청과 대조하고, API 계약, 백엔드 오류, DB 마이그레이션 상태를 필요한 만큼 조회합니다. 확인된 불일치에 한해 샘플 앱과 격리 SQLite에서 재현 테스트를 실행합니다.", body),
        Paragraph("검증된 데모", heading),
        Paragraph("① 필드명 불일치: 제보 500 / 관측 422, user_id / userId 대조<br/>② DB 마이그레이션 누락: V11 / V12, phone 컬럼 오류 확인<br/>③ 잘못된 제보·증거 부족: 원인을 꾸며내지 않고 제보를 정정하거나 추가 자료를 요청", body),
        Paragraph("실행 방법", heading),
        Paragraph("저장소 README의 Windows 실행 절차를 따라 NVIDIA API 키를 로컬 .env에 넣고 Streamlit 화면을 실행합니다. 코드와 합성 데이터는 저장소에 포함되어 있으며 API 키는 포함하지 않습니다.", body),
        Spacer(1, 18),
        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#C6D1DA")),
        Spacer(1, 8),
        Paragraph("재현 테스트는 샘플 코드와 격리 DB만 검증합니다. 실제 서비스 변경이나 운영 DB 수정은 수행하지 않습니다.", subtitle),
    ]
    doc = SimpleDocTemplate(
        str(output), pagesize=A4, rightMargin=48, leftMargin=48,
        topMargin=42, bottomMargin=42, title="TraceBridge - 온라인 사전 챌린지 포트폴리오",
        author=team_name,
    )
    doc.build(story)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("team_name")
    args = parser.parse_args()
    pdf = create_pdf(args.team_name)
    print(f"Created {pdf} ({pdf.stat().st_size:,} bytes)")
