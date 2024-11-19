import re
from pathlib import Path
from typing import IO

from dotenv import load_dotenv
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from unstructured.partition.auto import partition

from megaparse.core.parser import BaseParser
from megaparse.core.parser.type import StrategyEnum
from pypdf import PdfReader
import copy
import time


class UnstructuredParser(BaseParser):
    load_dotenv()

    def __init__(
        self, strategy=StrategyEnum.AUTO, model: BaseChatModel | None = None, **kwargs
    ):
        self.strategy = strategy
        self.model = model

    # Function to convert element category to markdown format
    def convert_to_markdown(self, elements):
        markdown_content = ""

        for el in elements:
            markdown_content += self.get_markdown_line(el)

        return markdown_content

    def get_markdown_line(self, el: dict):
        element_type = el["type"]
        text = el["text"]
        metadata = el["metadata"]
        parent_id = metadata.get("parent_id", None)
        category_depth = metadata.get("category_depth", 0)
        table_stack = []  # type: ignore

        # Markdown line defaults to empty
        markdown_line = ""

        # Element type-specific markdown content
        markdown_types = {
            "Title": f"## {text}\n\n" if parent_id else f"# {text}\n\n",
            "Subtitle": f"## {text}\n\n",
            "Header": f"{'#' * (category_depth + 1)} {text}\n\n",
            "Footer": f"#### {text}\n\n",
            "NarrativeText": f"{text}\n\n",
            "ListItem": f"- {text}\n",
            "Table": f"{text}\n\n",
            "PageBreak": "---\n\n",
            "Image": f"![Image]({el['metadata'].get('image_path', '')})\n\n",
            "Formula": f"$$ {text} $$\n\n",
            "FigureCaption": f"**Figure:** {text}\n\n",
            "Address": f"**Address:** {text}\n\n",
            "EmailAddress": f"**Email:** {text}\n\n",
            "CodeSnippet": f"```{el['metadata'].get('language', '')}\n{text}\n```\n\n",
            "PageNumber": "",  # Page number is not included in markdown
        }

        markdown_line = markdown_types.get(element_type, f"{text}\n\n")

        if element_type == "Table" and self.model:
            # FIXME: @Chloé - Add a modular table enhancement here - LVM
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "human",
                        """You are an expert in markdown tables, match this text and this html table to fill a md table. You answer with just the table in pure markdown, nothing else.
                        <TEXT>
                        {text}
                        </TEXT>
                        <HTML>
                        {html}
                        </HTML>
                        <PREVIOUS_TABLE>
                        {previous_table}
                        </PREVIOUS_TABLE>""",
                    ),
                ]
            )
            chain = prompt | self.model
            result = chain.invoke(
                {
                    "text": el["text"],
                    "html": metadata["text_as_html"],
                    "previous_table": table_stack[-1] if table_stack else "",
                }
            )
            content_str = (
                str(result.content)
                if not isinstance(result.content, str)
                else result.content
            )
            cleaned_content = re.sub(r"^```.*$\n?", "", content_str, flags=re.MULTILINE)
            markdown_line = f"[TABLE]\n{cleaned_content}\n[/TABLE]\n\n"

        return markdown_line

    def get_strategy(
        self,
        file_path_: str | Path | None = None,
        file_: IO[bytes] | None = None,
        threshold=0.5,
        page_threshold=0.8,
    ) -> StrategyEnum:
        t0 = time.perf_counter()
        file = copy.deepcopy(file_)
        file_path = copy.deepcopy(file_path_)

        if self.strategy != StrategyEnum.AUTO:
            raise ValueError("Strategy must be AUTO to use get_strategy")
        reader: PdfReader | None = None
        if file_path:
            reader = PdfReader(file_path)  # accepts stream bytes
        if file:
            reader = PdfReader(file)

        if reader is None:
            raise ValueError("No file or file path provided")

        image_proportion_per_pages = []

        for page in reader.pages:
            page_texts = page.extract_text()
            page_images = page.images
            image_proportion_per_pages.append(
                len(page_images) / (len(page_texts) + len(page_images))
            )
        total_proportion = sum(
            1 for prop in image_proportion_per_pages if prop > page_threshold
        ) / len(reader.pages)

        print(f"Time taken to get strategy: {time.perf_counter() - t0}")
        print(f"Total proportion of images: {total_proportion}")
        print(
            f"Mean Image proportion per page: {sum(image_proportion_per_pages) / len(image_proportion_per_pages)}"
        )

        if total_proportion > threshold:
            return StrategyEnum.HI_RES

        return StrategyEnum.FAST

    async def convert(
        self,
        file_path: str | Path | None = None,
        file: IO[bytes] | None = None,
        **kwargs,
    ) -> str:
        if self.strategy == StrategyEnum.AUTO:
            self.strategy = self.get_strategy(file_path_=file_path, file_=file)

        print(f"Strategy: {self.strategy}")
        # Partition the PDF
        elements = partition(
            filename=str(file_path) if file_path else None,
            file=file,
            strategy=self.strategy,
        )
        elements_dict = [el.to_dict() for el in elements]
        markdown_content = self.convert_to_markdown(elements_dict)
        return markdown_content
