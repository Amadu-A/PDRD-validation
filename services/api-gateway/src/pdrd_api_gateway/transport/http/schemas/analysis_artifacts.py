# services/api-gateway/src/pdrd_api_gateway/transport/http/schemas/analysis_artifacts.py

"""HTTP schemas внутренних reusable analysis artifacts."""

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
)

from pdrd_api_gateway.application.ports.analysis_visualization import (
    AnalysisBoundingBox,
    AnalysisPagePreview,
    AnalysisTextWord,
)


class AnalysisArtifactBoundingBoxPayload(BaseModel):
    """Нормализованный PDF bbox для persisted text geometry."""

    model_config = ConfigDict(
        extra="forbid",
    )

    x_min: int = Field(
        ge=0,
        le=1000,
    )
    y_min: int = Field(
        ge=0,
        le=1000,
    )
    x_max: int = Field(
        ge=0,
        le=1000,
    )
    y_max: int = Field(
        ge=0,
        le=1000,
    )

    def to_domain(
        self,
    ) -> AnalysisBoundingBox:
        """Преобразует wire bbox в application model."""
        return AnalysisBoundingBox(
            x_min=self.x_min,
            y_min=self.y_min,
            x_max=self.x_max,
            y_max=self.y_max,
        )


class AnalysisArtifactTextWordPayload(BaseModel):
    """Один positioned PDF word."""

    model_config = ConfigDict(
        extra="forbid",
    )

    text: str

    bbox: AnalysisArtifactBoundingBoxPayload

    block_no: int
    line_no: int
    word_no: int

    def to_domain(
        self,
    ) -> AnalysisTextWord:
        """Преобразует wire word в application model."""
        return AnalysisTextWord(
            text=self.text,
            bbox=self.bbox.to_domain(),
            block_no=self.block_no,
            line_no=self.line_no,
            word_no=self.word_no,
        )


class AnalysisVisualizationPagePayload(BaseModel):
    """Одна PDF-страница reusable visualization artifact."""

    model_config = ConfigDict(
        extra="forbid",
    )

    page_number: int = Field(
        ge=1,
    )

    width_points: float = Field(
        gt=0,
    )

    height_points: float = Field(
        gt=0,
    )

    image_base64: str = Field(
        min_length=1,
    )

    extracted_text: str = ""

    text_words: list[AnalysisArtifactTextWordPayload] = Field(
        default_factory=list,
    )

    def to_domain(
        self,
    ) -> AnalysisPagePreview:
        """Преобразует HTTP payload в application preview model."""
        return AnalysisPagePreview(
            page_number=self.page_number,
            width_points=self.width_points,
            height_points=self.height_points,
            image_base64=self.image_base64,
            extracted_text=self.extracted_text,
            text_words=tuple(word.to_domain() for word in self.text_words),
        )


class SaveAnalysisVisualizationArtifactRequest(BaseModel):
    """Запрос сохранения initial PDF extraction как reusable artifact."""

    model_config = ConfigDict(
        extra="forbid",
    )

    pages: list[AnalysisVisualizationPagePayload] = Field(
        min_length=1,
    )


class SaveAnalysisVisualizationArtifactResponse(BaseModel):
    """Результат сохранения reusable visualization artifact."""

    document_id: str

    pages_count: int = Field(
        ge=1,
    )
