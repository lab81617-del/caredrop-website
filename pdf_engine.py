import os
import tempfile
from datetime import datetime
from fpdf import FPDF
import qrcode
import barcode
from barcode.writer import ImageWriter

# ==========================================
# 1. CLINICAL MEDICAL REPORT PDF ENGINE
# ==========================================
class CareDropPDF(FPDF):
    def __init__(self, qr_path, bc_path, order_data, lab_data, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.qr_path = qr_path
        self.bc_path = bc_path
        self.order = order_data
        self.lab = lab_data if lab_data else {}
        self.set_auto_page_break(auto=True, margin=35)

    def header(self):
        # --- GLOBAL TOP BRANDING ---
        self.set_y(10)
        self.set_font("helvetica", "B", 22)
        self.set_text_color(13, 148, 136) 
        self.cell(120, 8, "CAREDROP DIAGNOSTICS", ln=False)
        
        if self.qr_path:
            self.image(self.qr_path, x=178, y=8, w=20)
            
        self.ln(6)
        self.set_font("helvetica", "B", 9)
        self.set_text_color(100, 100, 100)
        processing_lab = self.lab.get('name', 'Advanced Clinical Laboratory')
        self.cell(120, 5, f"Processed at: {processing_lab}", ln=True)
        
        self.set_draw_color(13, 148, 136)
        self.set_line_width(0.5)
        self.line(10, 24, 200, 24)
        self.set_line_width(0.2)
        self.ln(3)

        # --- PAGE 1: FULL PATIENT DEMOGRAPHICS ---
        if self.page_no() == 1:
            self.set_y(28)
            
            # Row 1
            self.set_font("helvetica", "", 9)
            self.set_text_color(100, 100, 100)
            self.cell(28, 6, "Patient Name", 0, 0)
            self.set_text_color(15, 23, 42)
            self.set_font("helvetica", "B", 10)
            self.cell(72, 6, f": {self.order.get('patient_name', 'N/A').title()}", 0, 0)
            
            self.set_font("helvetica", "", 9)
            self.set_text_color(100, 100, 100)
            self.cell(30, 6, "Registered On", 0, 0)
            self.set_text_color(15, 23, 42)
            self.set_font("helvetica", "B", 9)
            created = self.order.get('created_at') or datetime.today()
            self.cell(60, 6, f": {created.strftime('%d-%b-%Y %I:%M %p')}", 0, 1)

            # Row 2
            self.set_font("helvetica", "", 9)
            self.set_text_color(100, 100, 100)
            self.cell(28, 6, "Age / Gender", 0, 0)
            self.set_text_color(15, 23, 42)
            self.set_font("helvetica", "B", 9)
            self.cell(72, 6, f": {self.order.get('age', '--')} Yrs / {self.order.get('gender', '--')}", 0, 0)
            
            self.set_font("helvetica", "", 9)
            self.set_text_color(100, 100, 100)
            self.cell(30, 6, "Collected On", 0, 0)
            self.set_text_color(15, 23, 42)
            self.set_font("helvetica", "B", 9)
            self.cell(60, 6, f": {self.order.get('collection_date', '--')} {self.order.get('time_slot', '')}", 0, 1)

            # Row 3
            self.set_font("helvetica", "", 9)
            self.set_text_color(100, 100, 100)
            self.cell(28, 6, "Referred By", 0, 0)
            self.set_text_color(15, 23, 42)
            self.set_font("helvetica", "B", 9)
            self.cell(72, 6, f": {self.order.get('referred_by', 'Self').title()}", 0, 0)
            
            self.set_font("helvetica", "", 9)
            self.set_text_color(100, 100, 100)
            self.cell(30, 6, "Reported On", 0, 0)
            self.set_text_color(15, 23, 42)
            self.set_font("helvetica", "B", 9)
            self.cell(60, 6, f": {datetime.today().strftime('%d-%b-%Y %I:%M %p')}", 0, 1)
            
            # Row 4 (Barcode)
            self.set_font("helvetica", "", 9)
            self.set_text_color(100, 100, 100)
            self.cell(28, 8, "UID Barcode", 0, 0)
            self.cell(72, 8, ":", 0, 0)
            if self.bc_path:
                self.image(self.bc_path, x=40, y=self.get_y()+1, h=6)
            self.ln(10)
            
        # --- PAGE 2+: COMPRESSED MINI-HEADER ---
        else:
            self.set_y(26)
            self.set_font("helvetica", "B", 9)
            self.set_text_color(15, 23, 42)
            self.cell(100, 6, f"Patient: {self.order.get('patient_name', 'N/A').title()} ({self.order.get('patient_uid', '')})", 0, 0, 'L')
            self.cell(90, 6, f"Order: {self.order.get('order_ref', '')}", 0, 1, 'R')
            self.ln(2)

        self.set_draw_color(226, 232, 240)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(2)
        
        # --- 5-COLUMN TABLE HEADER ---
        self.set_font("helvetica", "B", 8)
        self.set_fill_color(248, 250, 252)
        self.set_text_color(100, 116, 139)
        self.cell(65, 8, ' INVESTIGATION', 0, 0, 'L', True)
        self.cell(20, 8, 'RESULT', 0, 0, 'C', True)
        self.cell(20, 8, 'UNIT', 0, 0, 'C', True)
        self.cell(45, 8, 'BIO. REF. INTERVAL', 0, 0, 'C', True)
        self.cell(40, 8, 'METHOD', 0, 1, 'C', True)
        self.ln(2)

    def footer(self):
        self.set_y(-30)
        self.set_draw_color(200, 200, 200)
        self.line(10, 267, 200, 267)
        
        doc_1_name = self.lab.get('doctor_1_name', 'Dr. Ram Shran')
        doc_1_deg = self.lab.get('doctor_1_degree', 'MBBS, MD (Pathology) | DMC-44740')
        doc_2_name = self.lab.get('doctor_2_name', 'Dr. Abdul Sameer Qureshi')
        doc_2_deg = self.lab.get('doctor_2_degree', 'MBBS, D.C.P | DMC-39510')

        self.set_y(-25)
        self.set_font("helvetica", "B", 10)
        self.set_text_color(15, 23, 42)
        self.cell(100, 5, doc_1_name, ln=False, align="L")
        self.cell(90, 5, doc_2_name, ln=True, align="R")
        
        self.set_font("helvetica", "", 8)
        self.set_text_color(100, 100, 100)
        self.cell(100, 4, doc_1_deg, ln=False, align="L")
        self.cell(90, 4, doc_2_deg, ln=True, align="R")
        
        self.set_y(-10)
        self.set_font("helvetica", "I", 8)
        self.cell(0, 5, f"Electronically processed. Page {self.page_no()}", align="C")


def generate_medical_report(order_id, order_data, results_data, lab_data):
    # 1. Pre-generate QR Code
    qr = qrcode.QRCode(box_size=4, border=0)
    qr.add_data(f"https://caredrop.in/download-report/{order_id}")
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#0F172A", back_color="white")
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tf_qr:
        qr_img.save(tf_qr, 'PNG')
        qr_path = tf_qr.name

    # 2. Pre-generate Barcode
    bc_img = barcode.get('code128', order_data.get('patient_uid', '0000'), writer=ImageWriter())
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tf_bc:
        bc_path = bc_img.save(tf_bc.name.replace('.png', ''))

    # 3. Initialize PDF
    pdf = CareDropPDF(qr_path=qr_path, bc_path=bc_path, order_data=order_data, lab_data=lab_data)
    pdf.add_page()
    
    current_cat, current_test = "", ""
    interpretations_to_print = []

    for r in results_data:
        # Category Headers
        if r['cat'] != current_cat:
            pdf.ln(2)
            pdf.set_font("helvetica", "B", 11)
            pdf.set_text_color(13, 148, 136)
            pdf.cell(190, 8, f"DEPARTMENT OF {r['cat'].upper()}", 0, 1, 'C')
            current_cat = r['cat']
            
        # Test Headers (Fixed Case)
        if r['test'] != current_test:
            pdf.set_font("helvetica", "B", 10)
            pdf.set_text_color(15, 23, 42)
            pdf.cell(190, 8, r['test'].upper(), 0, 1, 'L')
            current_test = r['test']
            
            # Store interpretation
            if r.get('interpretation'):
                if {'test': r['test'], 'text': r['interpretation']} not in interpretations_to_print:
                    interpretations_to_print.append({'test': r['test'], 'text': r['interpretation']})
        
        # String Truncation to prevent overlapping
        p_name = str(r['param'])[:35] + ("..." if len(str(r['param'])) > 35 else "")
        method_str = str(r.get('method') or '')[:25] + ("..." if len(str(r.get('method') or '')) > 25 else "")
        
        # Print Row
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(51, 65, 85)
        pdf.cell(65, 7, f"  {p_name}", 0, 0, 'L')
        
        pdf.set_font("helvetica", "B", 10)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(20, 7, str(r['val']), 0, 0, 'C')
        
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(20, 7, str(r['unit']), 0, 0, 'C')
        pdf.cell(45, 7, str(r['ref']), 0, 0, 'C')
        
        pdf.set_font("helvetica", "I", 8)
        pdf.cell(40, 7, method_str, 0, 1, 'C')
        
        pdf.set_draw_color(241, 245, 249)
        pdf.line(12, pdf.get_y(), 198, pdf.get_y())

    # 4. Print Interpretations
    if interpretations_to_print:
        pdf.ln(8)
        pdf.set_font("helvetica", "B", 10)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(190, 8, "CLINICAL INTERPRETATIONS", 0, 1, 'L')
        pdf.set_draw_color(13, 148, 136)
        pdf.line(10, pdf.get_y(), 50, pdf.get_y())
        pdf.ln(2)
        
        for interp in interpretations_to_print:
            pdf.set_font("helvetica", "B", 9)
            pdf.set_text_color(13, 148, 136)
            pdf.cell(190, 6, interp['test'].upper(), 0, 1, 'L')
            
            pdf.set_font("helvetica", "", 8)
            pdf.set_text_color(71, 85, 105)
            pdf.multi_cell(190, 4, interp['text'])
            pdf.ln(3)

    os.remove(qr_path)
    os.remove(bc_path)
    return pdf.output()

# ==========================================
# 2. FINANCIAL INVOICE PDF ENGINE
# ==========================================
class InvoicePDF(FPDF):
    def header(self):
        self.set_y(15)
        self.set_font("helvetica", "B", 24)
        self.set_text_color(15, 23, 42)
        self.cell(100, 10, "CAREDROP", ln=False)
        self.set_font("helvetica", "B", 14)
        self.set_text_color(13, 148, 136)
        self.cell(90, 10, "TAX INVOICE / RECEIPT", ln=True, align="R")
        
        self.set_font("helvetica", "", 9)
        self.set_text_color(100, 100, 100)
        self.cell(100, 5, "Advanced Clinical Laboratory Services", ln=False)
        self.cell(90, 5, "Original for Recipient", ln=True, align="R")
        
        self.ln(5)
        self.set_draw_color(226, 232, 240)
        self.line(10, 35, 200, 35)
        self.ln(5)

def generate_invoice_report(order_data, items_data):
    pdf = InvoicePDF()
    pdf.add_page()
    
    pdf.set_y(40)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(100, 6, "Billed To:", ln=False)
    pdf.cell(90, 6, "Invoice Details:", ln=True)
    
    pdf.set_font("helvetica", "", 10)
    pdf.set_text_color(71, 85, 105)
    pdf.cell(100, 6, f"Patient Name: {order_data['patient_name']}", ln=False)
    pdf.cell(90, 6, f"Order Ref: {order_data['order_ref']}", ln=True)
    
    pdf.cell(100, 6, f"Patient UID: {order_data['patient_uid']}", ln=False)
    created = order_data.get('created_at') or datetime.today()
    pdf.cell(90, 6, f"Invoice Date: {created.strftime('%d-%b-%Y')}", ln=True)
    
    pdf.cell(100, 6, f"Age/Gender: {order_data['age']} / {order_data['gender']}", ln=False)
    tpa = order_data.get('tpa_name')
    tpa_str = tpa if tpa else "Normal (Self Pay)"
    pdf.cell(90, 6, f"Panel/Insurance: {tpa_str}", ln=True)
    
    pdf.ln(10)
    
    pdf.set_font("helvetica", "B", 10)
    pdf.set_fill_color(241, 245, 249)
    pdf.set_text_color(15, 23, 42)
    pdf.set_draw_color(203, 213, 225)
    pdf.cell(15, 10, " S.No", border=1, align="C", fill=True)
    pdf.cell(140, 10, " Description of Service / Investigation", border=1, align="L", fill=True)
    pdf.cell(35, 10, " Amount (INR)", border=1, align="C", fill=True)
    pdf.ln(10)
    
    pdf.set_font("helvetica", "", 10)
    for index, item in enumerate(items_data, 1):
        pdf.cell(15, 10, f" {index}", border=1, align="C")
        pdf.cell(140, 10, f" {item['test_name'].title()}", border=1, align="L")
        pdf.cell(35, 10, f" {int(item['price'])}.00", border=1, align="C")
        pdf.ln(10)
        
    pdf.set_font("helvetica", "B", 10)
    pdf.set_fill_color(248, 250, 252)
    
    total = int(order_data.get('total_amount') or 0)
    advance = int(order_data.get('advance_amount') or 0)
    balance = int(order_data.get('balance_amount') or 0)
    
    pdf.cell(155, 8, " TOTAL AMOUNT:", border=1, align="R", fill=True)
    pdf.cell(35, 8, f" Rs. {total}.00", border=1, align="C", fill=True)
    pdf.ln()
    
    pdf.cell(155, 8, " ADVANCE PAID:", border=1, align="R", fill=True)
    pdf.cell(35, 8, f" Rs. {advance}.00", border=1, align="C", fill=True)
    pdf.ln()
    
    pdf.set_text_color(220, 38, 38) if balance > 0 else pdf.set_text_color(22, 163, 74)
    pdf.cell(155, 8, " PENDING BALANCE:", border=1, align="R", fill=True)
    pdf.cell(35, 8, f" Rs. {balance}.00", border=1, align="C", fill=True)
    
    pdf.ln(25)
    pdf.set_font("helvetica", "I", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 5, "This is a computer-generated invoice. No physical signature is required.", align="C", ln=True)
    
    return pdf.output()
