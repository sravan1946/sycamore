from abc import ABC, abstractmethod
from typing import Optional, Union

from sycamore.data.document import Document, HierarchicalDocument
from sycamore.plan_nodes import Node
from sycamore.transforms.map import Map


class DocumentStructure(ABC):
    def __init__(self):
        pass

    @staticmethod
    @abstractmethod
    def extract(document: Document) -> Union[Document, HierarchicalDocument]:
        pass


class StructureBySection(DocumentStructure):
    """
    Extracts the structure of the document organizing document elements by their
    respective section headers.
    """

    @staticmethod
    def extract(doc: Document) -> HierarchicalDocument:
        import uuid

        doc = HierarchicalDocument(doc.data)
        # if the first element is not a section header, insert generic placeholder
        if len(doc.children) > 0 and doc.children[0]["type"] != "Section-header":
            initial_page = HierarchicalDocument(
                {
                    "type": "Section-header",
                    "bbox": (0, 0, 0, 0),
                    "properties": {"score": 1, "page_number": 1},
                    "text_representation": "Front Page",
                    "binary_representation": b"Front Page",
                }
            )
            doc.children.insert(0, initial_page)  # O(n) insert :( we should use deque for everything

        doc.data["relationships"] = doc.get("relationships", {})
        doc.data["label"] = doc.get("label", "DOCUMENT")

        sections = []

        section: Optional[HierarchicalDocument] = None
        element: Optional[HierarchicalDocument] = None
        for child in doc.children:
            child.data["relationships"] = child.get("relationships", {})
            if child.type == "Section-header" and child.data.get("text_representation"):
                if section is not None:
                    next = {
                        "TYPE": "NEXT",
                        "properties": {},
                        "START_ID": section.doc_id,
                        "START_LABEL": "SECTION",
                        "END_ID": child.doc_id,
                        "END_LABEL": "SECTION",
                    }
                    child.data["relationships"][str(uuid.uuid4())] = next
                    element = None
                rel = {
                    "TYPE": "SECTION_OF",
                    "properties": {},
                    "START_ID": child.doc_id,
                    "START_LABEL": "SECTION",
                    "END_ID": doc.doc_id,
                    "END_LABEL": "DOCUMENT",
                }
                child.data["relationships"][str(uuid.uuid4())] = rel
                child.data["label"] = "SECTION"
                section = child
                sections.append(section)
            else:
                assert section is not None
                if element is not None:
                    next = {
                        "TYPE": "NEXT",
                        "properties": {},
                        "START_ID": element.doc_id,
                        "START_LABEL": "ELEMENT",
                        "END_ID": child.doc_id,
                        "END_LABEL": "ELEMENT",
                    }
                    child.data["relationships"][str(uuid.uuid4())] = next
                rel = {
                    "TYPE": "PART_OF",
                    "properties": {},
                    "START_ID": child.doc_id,
                    "START_LABEL": "ELEMENT",
                    "END_ID": section.doc_id,
                    "END_LABEL": "SECTION",
                }
                child.data["relationships"][str(uuid.uuid4())] = rel
                child.data["label"] = "ELEMENT"
                element = child
                section.data["children"].append(element)

        doc.children = sections
        return doc


class ExtractDocumentStructure(Map):
    """
    extracting structure
    """

    def __init__(
        self,
        child: Node,
        structure: DocumentStructure,
        **resource_args,
    ):
        super().__init__(child, f=structure.extract, **resource_args)


class ExtractSummaries(Map):
    """
    Extracts summaries from child documents to be used for entity extraction. This function
    generates summaries for sections within documents which are used during entity extraction.
    """

    def __init__(self, child: Node, **resource_args):
        super().__init__(child, f=ExtractSummaries.summarize_sections, **resource_args)

    @staticmethod
    def summarize_sections(doc: HierarchicalDocument) -> HierarchicalDocument:
        for section in doc.children:
            assert section.text_representation is not None
            summary_list = []
            sec_sum = f"-----SECTION TITLE: {section.text_representation.strip()}-----\n"
            summary_list.append(sec_sum)
            for element in section.children:
                assert element.type is not None
                assert element.text_representation is not None
                elem_sum = f"---Element Type: {element.type.strip()}---\n{element.text_representation.strip()}\n"
                summary_list.append(elem_sum)
            section.data["summary"] = "".join(summary_list)
        return doc
