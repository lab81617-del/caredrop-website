import os
import tempfile
from datetime import datetime
from fpdf import FPDF
import qrcode
import barcode
from barcode.writer import ImageWriter

class CareDropPDF(FPDF):
    def __init__(self, qr_path, order_data, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.qr_path = qr_path
        self.order = order_data
        self.set_auto_page_break(auto=True, margin=35)

    def header(self):
        # 1. Proprietary CareDrop Header
        self.set_y(12)
        self.set_font("helvetica", "B", 24)
        self.set_text_color(13, 148, 136) # CareDrop Teal
        self.cell(120, 10, "CAREDROP DIAGNOSTICS", ln=False)
        
        # QR Code strictly at Top Right
        if self.qr_path:
            self.image(self.qr_path, x=175, y=8, w=22)
            
        self.ln(8)
        self.set_font("helvetica", "B", 9)
        self.set_text_color(100, 100, 100)
        self.cell(120, 5, "Advanced Clinical Laboratory | Precision & Care", ln=True)
        
        self.set_draw_color(13, 148, 136)
        self.set_line_width(0.5)
        self.line(10, 28, 200, 28)
        self.set_line_width(0.2)
        self.ln(5)

        # 2. Patient Demographics Grid (Prints on every page)
        self.set_y(32)
        self.set_font("helvetica", "", 9)
        self.set_text_color(100, 100, 100)
        
        # Left Column
        self.cell(30, 6, "Patient Name", 0, 0)
        self.set_text_color(15, 23, 42); self.set_font("helvetica", "B", 10)
        self.cell(70, 6, f": {self.order.get('patient_name', 'N/A').title()}", 0, 0)
        
        # Right Column
        self.set_font("helvetica", "", 9); self.set_text_color(100, 100, 100)
        self.cell(35, 6, "Registered On", 0, 0)
        self.set_text_color(15, 23, 42); self.set_font("helvetica", "B", 9)
        created = self.order.get('created_at') or datetime.today()
        self.cell(55, 6, f": {created.strftime('%d-%b-%Y %I:%M %p')}", 0, 1)

        # Left Column
        self.set_font("helvetica", "", 9); self.set_text_color(100, 100, 100)
        self.cell(30, 6, "Age / Gender", 0, 0)
        self.set_text_color(15, 23, 42); self.set_font("helvetica", "B", 9)
        self.cell(70, 6, f": {self.order.get('age', '--')} Yrs / {self.order.get('gender', '--')}", 0, 0)
        
        # Right Column
        self.set_font("helvetica", "", 9); self.set_text_color(100, 100, 100)
        self.cell(35, 6, "Collected On", 0, 0)
        self.set_text_color(15, 23, 42); self.set_font("helvetica", "B", 9)
        self.cell(55, 6, f": {self.order.get('collection_date', '--')} {self.order.get('time_slot', '')}", 0, 1)

        # Left Column
        self.set_font("helvetica", "", 9); self.set_text_color(100, 100, 100)
        self.cell(30, 6, "Referred By", 0, 0)
        self.set_text_color(15, 23, 42); self.set_font("helvetica", "B", 9)
        self.cell(70, 6, f": {self.order.get('referred_by', 'Self').title()}", 0, 0)
        
        # Right Column
        self.set_font("helvetica", "", 9); self.set_text_color(100, 100, 100)
        self.cell(35, 6, "Reported On", 0, 0)
        self.set_text_color(15, 23, 42); self.set_font("helvetica", "B", 9)
        self.cell(55, 6, f": {datetime.today().strftime('%d-%b-%Y %I:%M %p')}", 0, 1)
        
        # Barcode Row
        self.set_font("helvetica", "", 9); self.set_text_color(100, 100, 100)
        self.cell(30, 8, "UID Barcode", 0, 0)
        self.cell(70, 8, ":", 0, 0)
        
        # Generate & Place Barcode
        bc_img = barcode.get('code128', self.order.get('patient_uid', '0000'), writer=ImageWriter())
        with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tf_bc:
            bc_path = bc_img.save(tf_bc.name.replace('.png', ''))
            self.image(bc_path, x=42, y=self.get_y(), h=7)
        os.remove(bc_path)
        
        self.ln(10)
        self.set_draw_color(226, 232, 240)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)
        
        # 3. Table Header
        self.set_font("helvetica", "B", 9)
        self.set_fill_color(248, 250, 252)
        self.set_text_color(100, 116, 139)
        self.cell(85, 8, ' INVESTIGATION', 0, 0, 'L', True)
        self.cell(25, 8, 'RESULT', 0, 0, 'C', True)
        self.cell(30, 8, 'UNIT', 0, 0, 'C', True)
        self.cell(50, 8, 'BIO. REF. INTERVAL', 0, 1, 'C', True)
        self.ln(2)

    def footer(self):
        self.set_y(-30)
        self.set_draw_color(200, 200, 200)
        self.line(10, 267, 200, 267)
        
        self.set_y(-25)
        self.set_font("helvetica", "B", 10)
        self.set_text_color(15, 23, 42)
        self.cell(100, 5, "CareDrop Digital Verification", ln=False, align="L")
        self.cell(90, 5, "Chief Laboratory Director", ln=True, align="R")
        
        self.set_font("helvetica", "", 8)
        self.set_text_color(100, 100, 100)
        self.cell(100, 4, "Electronically processed. No physical signature required.", ln=False, align="L")
        self.cell(90, 4, "Verified by CareDrop LIMS", ln=True, align="R")
        
        self.set_y(-10)
        self.set_font("helvetica", "I", 8)
        self.cell(0, 5, f"Page {self.page_no()}", align="C")

def generate_medical_report(order_id, order_data, results_data):
    # 1. Create QR Code
    qr = qrcode.QRCode(box_size=4, border=0)
    qr.add_data(f"https://caredrop.in/download-report/{order_id}")
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#0F172A", back_color="white")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tf_qr:
        qr_img.save(tf_qr, 'PNG')
        qr_path = tf_qr.name

    # 2. Initialize PDF
    pdf = CareDropPDF(qr_path=qr_path, order_data=order_data)
    pdf.add_page()
    
    # 3. Print Clinical Data
    current_cat, current_test = "", ""
    for r in results_data:
        # Category Header (e.g., DEPARTMENT OF HAEMATOLOGY)
        if r['cat'] != current_cat:
            pdf.ln(4)
            pdf.set_font("helvetica", "BU", 10)
            pdf.set_text_color(13, 148, 136)
            pdf.cell(190, 8, f"DEPARTMENT OF {r['cat'].upper()}", 0, 1, 'C')
            current_cat = r['cat']
            
        # Test Header (e.g., Complete Blood Count)
        if r['test'] != current_test:
            pdf.set_font("helvetica", "B", 10)
            pdf.set_text_color(15, 23, 42)
            pdf.cell(190, 8, r['test'].title(), 0, 1, 'L')
            current_test = r['test']
        
        # Parameter Row
        pdf.set_font("helvetica", "", 10)
        pdf.set_text_color(51, 65, 85)
        pdf.cell(85, 7, f"  {r['param']}", 0, 0, 'L')
        
        # Result Value (Bold)
        pdf.set_font("helvetica", "B", 10)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(25, 7, str(r['val']), 0, 0, 'C')
        
        # Unit & Reference
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(30, 7, str(r['unit']), 0, 0, 'C')
        pdf.cell(50, 7, str(r['ref']), 0, 1, 'C')
        
        # Faint line separator
        pdf.set_draw_color(241, 245, 249)
        pdf.line(12, pdf.get_y(), 198, pdf.get_y())

    # 4. Cleanup & Output
    os.remove(qr_path)
    return pdf.output()
