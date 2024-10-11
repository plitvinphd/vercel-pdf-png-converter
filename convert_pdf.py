import os
import base64
import logging
import asyncio
import fitz  # PyMuPDF
import aiohttp
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, HttpUrl
from typing import List

# Initialize the FastAPI app
app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO)

# Environment variable for Imgbb API key
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY")
if not IMGBB_API_KEY:
    raise Exception("Imgbb API key not found in environment variables.")


class PDFUrl(BaseModel):
    url: HttpUrl


async def download_pdf(url: str) -> bytes:
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, allow_redirects=True) as response:
                logging.info(f"Response status: {response.status}")
                logging.info(f"Response headers: {response.headers}")
                if response.status != 200:
                    raise HTTPException(status_code=400,
                                        detail=f"Failed to download PDF. Status code: {response.status}")
                content_type = response.headers.get('Content-Type', '')
                logging.info(f"Content-Type: {content_type}")
                if 'pdf' not in content_type.lower():
                    raise HTTPException(status_code=400,
                                        detail=f"URL does not point to a PDF file. Content-Type: {content_type}")
                pdf_bytes = await response.read()
                # Limit file size to 10 MB
                if len(pdf_bytes) > 10 * 1024 * 1024:
                    raise HTTPException(status_code=400, detail="PDF file is too large.")
                return pdf_bytes
    except aiohttp.ClientError as e:
        logging.error(f"Client error: {e}")
        raise HTTPException(status_code=400, detail="Client error occurred while downloading PDF.")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail="Unexpected error occurred.")


async def convert_pdf_to_images(pdf_bytes: bytes) -> List[bytes]:
    try:
        images = []
        # Open the PDF from bytes
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                pix = page.get_pixmap()
                image_bytes = pix.tobytes("png")
                images.append(image_bytes)
        return images
    except Exception as e:
        logging.error(f"Error converting PDF to images: {e}")
        raise HTTPException(status_code=500, detail="Error converting PDF to images.")


async def upload_image_to_imgbb(image_bytes: bytes) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            encoded_image = base64.b64encode(image_bytes).decode('utf-8')
            data = {
                'key': IMGBB_API_KEY,
                'image': encoded_image,
            }
            async with session.post('https://api.imgbb.com/1/upload', data=data) as response:
                result = await response.json()
                if 'data' in result and 'url' in result['data']:
                    return result['data']['url']
                else:
                    logging.error(f"Error uploading image: {result}")
                    raise HTTPException(status_code=500, detail="Error uploading image.")
    except Exception as e:
        logging.error(f"Error uploading image to Imgbb: {e}")
        raise HTTPException(status_code=500, detail="Error uploading images.")


@app.post("/api/convert-pdf")
async def convert_pdf_endpoint(pdf: PDFUrl):
    try:
        pdf_bytes = await download_pdf(str(pdf.url))
        image_bytes_list = await convert_pdf_to_images(pdf_bytes)

        # Upload images concurrently
        upload_tasks = [upload_image_to_imgbb(image_bytes) for image_bytes in image_bytes_list]
        image_urls = await asyncio.gather(*upload_tasks)

        if not image_urls:
            raise HTTPException(status_code=500, detail="No images were generated.")

        return {"images": image_urls}
    except HTTPException as http_exc:
        # Re-raise HTTP exceptions to be handled by FastAPI
        raise http_exc
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=str(e))