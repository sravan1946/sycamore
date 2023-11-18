import React from 'react';
import { Anchor, AppShell, Button, Center, Container, Group, Loader, Text } from '@mantine/core';
import { useLocation, useSearchParams } from 'react-router-dom';
import { useState, useEffect } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/TextLayer.css';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import "pdfjs-dist/build/pdf.worker.entry";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
    'pdfjs-dist/build/pdf.worker.js',
    import.meta.url,
).toString();

const HOST = "http://localhost:3001"

function isValid(left: string, top: string, ranges = []) {
    const l = parseFloat(left.replace('%', '')) * 0.01;
    const t = parseFloat(top.replace('%', '')) * 0.01;
    for (let range of ranges) {
        console.log(range[0], range[1], range[2], range[3])
        if (range[0] < l && l < range[1] && range[2] < t && t < range[3]) {
            return true;
        }
    }
    return false;
}

const fetchPDFThroughProxy = async (url: string) => {
    try {
        console.log("Getting pdf for: ", url)
        const response = await fetch(HOST + '/v1/pdf', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                url: url
            })
        });

        if (!response.ok) {
            throw new Error('Network response was not ok');
        }

        const pdfBlob = await response.blob();
        return URL.createObjectURL(pdfBlob);
    } catch (error) {
        console.error('Error fetching PDF through proxy:', error);
        throw error;
    }
};

export default function PdfViewer() {
    const [numPages, setNumPages] = useState<number>(-1);
    const [pageNumber, setPageNumber] = useState<number>(1);
    const [url, setUrl] = useState("");
    const [originalUrl, setOriginalUrl] = useState("");
    const [title, setTitle] = useState("Untitled");
    const [id, setId] = useState("");
    const [boxes, setBoxes] = useState<any>()
    const [loading, setLoading] = useState(true)


    useEffect(() => {
        const loadPdf = async () => {
            console.log("Loading metadata")
            const dataString: string = localStorage.getItem('pdfDocumentMetadata') ?? "";
            const pdfDocumentMetadata: any = JSON.parse(dataString);

            setId(pdfDocumentMetadata.id);
            setTitle(pdfDocumentMetadata.title);
            setOriginalUrl(pdfDocumentMetadata.url)
            const proxiedUrl = await fetchPDFThroughProxy(pdfDocumentMetadata.url);
            const response = await proxiedUrl;
            setUrl(response);

            const pageNum : number = pdfDocumentMetadata.properties.page_number;
            let boxObj : any = null;
            const bbox : number[] = pdfDocumentMetadata.bbox;
            if (bbox) {
                // This is overly complex.  We can simplify when we
                // drop the backward compatibility stuff below.
                boxObj = { [pageNum]: [bbox] };
            }
            else {
                boxObj = pdfDocumentMetadata.properties.boxes ??
                    pdfDocumentMetadata.properties.coordinates.points;
            }
            if (boxObj) {
                setBoxes(boxObj)
                let firstPage = Math.min(...Object.keys(boxObj).map(Number));
                if (!firstPage) {
                    firstPage = pageNum;
                    if (!firstPage) {
                       firstPage = 1;
                    }
                }
                setPageNumber(firstPage);
            }
            else {
                setPageNumber(1); // i guess
            }

            setLoading(false)
        }
        loadPdf();
    }, []);

    function onDocumentLoadSuccess({ numPages }: { numPages: number }): void {
        setNumPages(numPages);
    }
    const goToPrevPage = () =>
        setPageNumber(pageNumber - 1 <= 1 ? 1 : pageNumber - 1);

    const goToNextPage = () =>
        setPageNumber(pageNumber + 1 >= numPages ? numPages : pageNumber + 1);

    return (
        <AppShell
            p="lg"
            header={
                <Center>

                    <Container>
                        {!loading &&
                            <Group grow>
                                <Button variant="subtle" color="indigo" size="xs" onClick={goToPrevPage}>{"<"} Prev</Button>
                                <Text fz="xs">Page {pageNumber} of {numPages}</Text>
                                <Button variant="subtle" color="indigo" size="xs" onClick={goToNextPage}>Next{">"}</Button>
                            </Group>
                        }
                        <Text fw="600" ta="center" mt="2rem">
                            {title}
                        </Text>
                        <Anchor fz="xs" ta="center" href={originalUrl} target='_blank'>
                            {originalUrl}
                        </Anchor>
                        <Text fz="xs" ta="center">
                            search document id: {id}
                        </Text>
                    </Container>
                </Center>

            }>
            <Center>
                {loading && <Loader />}
                {!loading &&
                    <Container>
                        <Document file={url} onLoadSuccess={onDocumentLoadSuccess}>
                            <Page pageNumber={pageNumber}>
                                {boxes[pageNumber] && boxes[pageNumber].map((box: any, index: number) => (
                                    <div
                                        key={index}
                                        style={{
                                            position: "absolute",
                                            backgroundColor: "#ffff0033",
                                            left: `${box[0] * 100}%`,
                                            top: `${box[1] * 100}%`,
                                            width: `${(box[2] - box[0]) * 100}%`,
                                            height: `${(box[3] - box[1]) * 100}%`,
                                        }} />
                                ))}
                            </Page>
                        </Document>
                    </Container>
                }
            </Center>
        </AppShell >
    );

};