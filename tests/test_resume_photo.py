"""Photo extraction against real embedded-image PDF pages."""

import base64
import os
import tempfile
import unittest
import zlib
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from PyPDF2 import PdfWriter
from PyPDF2.generic import (
    DecodedStreamObject,
    DictionaryObject,
    EncodedStreamObject,
    NameObject,
    NumberObject,
)

from pbl_jobs_finder.modules.resume_pdf import (
    ResumeBasics,
    ResumeDocument,
    _find_scanned_photo_box,
    create_resume_pdf,
    extract_resume_photo_data_uri,
)


def write_photo_pdf(
    path: Path,
    boxes: list[tuple[int, int, int, int]],
    *,
    source: Image.Image | None = None,
    jpeg: bool = False,
    rotation: int = 0,
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=840)
    page = writer.pages[0]
    if rotation:
        page.rotate(rotation)
    images = DictionaryObject()
    commands = []
    for index, (left, bottom, width, height) in enumerate(boxes):
        with (
            source.copy()
            if source is not None
            else Image.new("RGB", (180, 240), (40, 100, 180)) as photo
        ):
            image = EncodedStreamObject()
            image.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(photo.width),
                    NameObject("/Height"): NumberObject(photo.height),
                    NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                    NameObject("/BitsPerComponent"): NumberObject(8),
                    NameObject("/Filter"): NameObject("/FlateDecode"),
                }
            )
            if jpeg:
                encoded = BytesIO()
                photo.convert("RGB").save(encoded, format="JPEG", quality=95)
                image[NameObject("/Filter")] = NameObject("/DCTDecode")
                image._data = encoded.getvalue()
            else:
                image._data = zlib.compress(photo.convert("RGB").tobytes())
            if photo.mode == "RGBA":
                mask = EncodedStreamObject()
                mask.update(
                    {
                        NameObject("/Type"): NameObject("/XObject"),
                        NameObject("/Subtype"): NameObject("/Image"),
                        NameObject("/Width"): NumberObject(photo.width),
                        NameObject("/Height"): NumberObject(photo.height),
                        NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                        NameObject("/BitsPerComponent"): NumberObject(8),
                        NameObject("/Filter"): NameObject("/FlateDecode"),
                    }
                )
                mask._data = zlib.compress(photo.getchannel("A").tobytes())
                image[NameObject("/SMask")] = writer._add_object(mask)
        images[NameObject(f"/Photo{index}")] = writer._add_object(image)
        commands.append(f"q {width} 0 0 {height} {left} {bottom} cm /Photo{index} Do Q")
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): images})
    content = DecodedStreamObject()
    content.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as stream:
        writer.write(stream)


class ResumePhotoExtractionTests(unittest.TestCase):
    @unittest.skipUnless(
        os.getenv("RUN_PLAYWRIGHT_PDF_TESTS") == "1",
        "set RUN_PLAYWRIGHT_PDF_TESTS=1 for the Chromium integration test",
    )
    def test_recovered_photo_pixels_survive_actual_pdf_export(self) -> None:
        import pypdfium2 as pdfium

        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "original.pdf"
            write_photo_pdf(original, [(480, 680, 90, 120)])
            uri = extract_resume_photo_data_uri(original)
            resume = ResumeDocument(
                basics=ResumeBasics(name="Test", phone="13800138000")
            )
            destination = create_resume_pdf(
                resume, "Test", directory, photo_data_uri=uri
            )
            with pdfium.PdfDocument(destination) as document:
                page = document[0]
                try:
                    bitmap = page.render(scale=1)
                    try:
                        pixels = bitmap.to_pil().convert("RGB").getdata()
                        photo_pixels = sum(
                            all(abs(a - b) < 5 for a, b in zip(pixel, (40, 100, 180)))
                            for pixel in pixels
                        )
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            self.assertGreater(photo_pixels, 1000)

    def test_scanned_photo_uses_frame_bounds_instead_of_just_the_face(self) -> None:
        with Image.new("RGB", (1200, 1680), "white") as page:
            page.paste((40, 100, 180), (960, 130, 1110, 330))
            detector = Mock()
            detector.detectMultiScale.return_value = [(995, 165, 70, 70)]
            with patch("cv2.CascadeClassifier", return_value=detector):
                self.assertEqual(_find_scanned_photo_box(page), (960, 130, 1110, 330))

    def test_scanned_text_or_multiple_faces_are_not_used_as_a_photo(self) -> None:
        with Image.new("RGB", (1200, 1680), "white") as page:
            for faces in (
                [],
                [(995, 165, 70, 70)],
                [(995, 165, 70, 70), (40, 165, 70, 70)],
            ):
                detector = Mock()
                detector.detectMultiScale.return_value = faces
                with (
                    self.subTest(faces=faces),
                    patch("cv2.CascadeClassifier", return_value=detector),
                ):
                    self.assertIsNone(_find_scanned_photo_box(page))

    def test_header_photo_is_recovered_as_a_lossless_png(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "resume.pdf"
            write_photo_pdf(path, [(480, 680, 90, 120)])
            uri = extract_resume_photo_data_uri(path)
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as photo:
            self.assertEqual(photo.size, (180, 240))
            self.assertEqual(photo.convert("RGB").getpixel((90, 120)), (40, 100, 180))

    def test_high_resolution_photo_keeps_every_original_pixel(self) -> None:
        with Image.new("RGB", (1500, 2000), (40, 100, 180)) as original:
            original.paste((250, 31, 79), (217, 149, 733, 1623))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "resume.pdf"
                write_photo_pdf(path, [(480, 680, 90, 120)], source=original)
                with patch(
                    "pypdfium2.PdfPage.render",
                    side_effect=AssertionError("page rendering loses resolution"),
                ):
                    uri = extract_resume_photo_data_uri(path)
            with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as photo:
                self.assertEqual(photo.size, original.size)
                self.assertEqual(photo.convert("RGB").tobytes(), original.tobytes())

    def test_scan_crop_uses_original_image_coordinates_and_pixels(self) -> None:
        with Image.new("RGB", (992, 1403), "white") as scan:
            scan.paste((41, 103, 179), (797, 107, 918, 272))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "scan.pdf"
                write_photo_pdf(path, [(0, 0, 600, 840)], source=scan)
                with patch(
                    "pbl_jobs_finder.modules.resume_pdf._find_scanned_photo_box",
                    return_value=(797, 107, 918, 272),
                ) as detect:
                    uri = extract_resume_photo_data_uri(path)
                    self.assertEqual(detect.call_args.args[0].size, scan.size)
            with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as photo:
                self.assertEqual(photo.format, "PNG")
                self.assertEqual(photo.size, (121, 165))
                self.assertEqual(
                    photo.convert("RGB").tobytes(),
                    scan.crop((797, 107, 918, 272)).tobytes(),
                )

    def test_jpeg_image_is_decoded_at_original_resolution_and_saved_as_png(
        self,
    ) -> None:
        with Image.new("RGB", (900, 1200), (40, 100, 180)) as original:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "jpeg.pdf"
                write_photo_pdf(path, [(480, 680, 90, 120)], source=original, jpeg=True)
                uri = extract_resume_photo_data_uri(path)
            with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as photo:
                self.assertEqual(photo.format, "PNG")
                self.assertEqual(photo.size, original.size)

    def test_transparency_is_preserved_in_native_png(self) -> None:
        with Image.new("RGBA", (180, 240), (40, 100, 180, 255)) as original:
            original.paste((40, 100, 180, 0), (0, 0, 50, 80))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "alpha.pdf"
                write_photo_pdf(path, [(480, 680, 90, 120)], source=original)
                uri = extract_resume_photo_data_uri(path)
            with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as photo:
                self.assertEqual(
                    photo.getchannel("A").tobytes(), original.getchannel("A").tobytes()
                )
                self.assertEqual(photo.getpixel((100, 100)), (40, 100, 180, 255))

    def test_page_rotation_preserves_photo_orientation_without_resampling(self) -> None:
        with Image.new("RGB", (180, 240), (40, 100, 180)) as original:
            original.paste((200, 30, 60), (0, 0, 90, 120))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "rotated.pdf"
                write_photo_pdf(
                    path, [(30, 40, 90, 120)], source=original, rotation=180
                )
                uri = extract_resume_photo_data_uri(path)
            with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as photo:
                self.assertEqual(
                    photo.convert("RGB").tobytes(),
                    original.transpose(Image.Transpose.ROTATE_180).tobytes(),
                )

    def test_scans_icons_body_images_and_ambiguous_headers_are_not_portraits(
        self,
    ) -> None:
        for boxes in (
            [],
            [(0, 0, 600, 840)],
            [(500, 780, 20, 20)],
            [(480, 300, 90, 120)],
            [(400, 720, 160, 60)],
            [(480, 680, 90, 120), (30, 680, 90, 120)],
        ):
            with self.subTest(boxes=boxes), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "resume.pdf"
                write_photo_pdf(path, boxes)
                self.assertEqual(extract_resume_photo_data_uri(path), "")

    def test_unreadable_pdf_does_not_break_optional_photo_recovery(self) -> None:
        self.assertEqual(extract_resume_photo_data_uri("missing-resume.pdf"), "")
