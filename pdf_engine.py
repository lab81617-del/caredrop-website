import os
import tempfile
from datetime import datetime
from fpdf import FPDF
import qrcode
import barcode
from barcode.writer import ImageWriter

# ==========================================
# 1. ULTRA-PREMIUM CLINICAL REPORT ENGINE
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
        # --- EDGE-TO-EDGE PREMIUM HEADER BAR ---
        self.set_fill_color(13, 148, 136) # Clinical Teal
        self.rect(0, 0, 210, 20, 'F')
        
        self.set_y(5)
        self.set_font("helvetica", "B", 24)
        self.set_text_color(255, 255, 255)
        self.cell(130, 10, "  CAREDROP DIAGNOSTICS", 0, 0, 'L')
        
        self.set_font("helvetica", "B", 10)
        self.cell(70, 10, "CLINICAL LABORATORY REPORT  ", 0, 1, 'R')
        
        self.set_y(25)
        self.set_font("helvetica", "B", 9)
        self.set_text_color(15, 23, 42)
        processing_lab = self.lab.get('name', 'Advanced Clinical Laboratory')
        self.cell(0, 6, f"Processed Exclusively At: {processing_lab}", 0, 1, 'C')
        self.ln(2)

        # --- PAGE 1: STRUCTURED PATIENT DEMOGRAPHICS GRID ---
        if self.page_no() == 1:
            self.set_y(35)
            
            # Outer Box
            self.set_draw_color(203, 213, 225)
            self.set_line_width(0.3)
            self.rect(10, 35, 190, 42)
            
            # Vertical Divider
            self.line(105, 35, 105, 77)

            # Left Column: Demographics
            self.set_y(37)
            self.set_font("helvetica", "B", 8)
            self.set_text_color(100, 116, 139)
            self.cell(30, 6, "  Patient Name", 0, 0)
            self.set_font("helvetica", "B", 10)
            self.set_text_color(15, 23, 42)
            self.cell(65, 6, f":  {self.order.get('patient_name', 'N/A').title()}", 0, 0)
            
            # Right Column: Timestamps
            self.set_font("helvetica", "B", 8)
            self.set_text_color(100, 116, 139)
            self.cell(30, 6, "  Registered On", 0, 0)
            self.set_font("helvetica", "B", 9)
            self.set_text_color(15, 23, 42)
            created = self.order.get('created_at') or datetime.today()
            self.cell(65, 6, f":  {created.strftime('%d-%b-%Y %I:%M %p')}", 0, 1)

            # Left Row 2
            self.set_font("helvetica", "B", 8)
            self.set_text_color(100, 116, 139)
            self.cell(30, 6, "  Age / Gender", 0, 0)
            self.set_font("helvetica", "B", 9)
            self.set_text_color(15, 23, 42)
            self.cell(65, 6, f":  {self.order.get('age', '--')} Yrs / {self.order.get('gender', '--')}", 0, 0)
            
            # Right Row 2
            self.set_font("helvetica", "B", 8)
            self.set_text_color(100, 116, 139)
            self.cell(30, 6, "  Collected On", 0, 0)
            self.set_font("helvetica", "B", 9)
            self.set_text_color(15, 23, 42)
            self.cell(65, 6, f":  {self.order.get('collection_date', '--')} {self.order.get('time_slot', '')}", 0, 1)

            # Left Row 3
            self.set_font("helvetica", "B", 8)
            self.set_text_color(100, 116, 139)
            self.cell(30, 6, "  Referred By", 0, 0)
            self.set_font("helvetica", "B", 9)
            self.set_text_color(15, 23, 42)
            self.cell(65, 6, f":  {self.order.get('referred_by', 'Self').title()}", 0, 0)
            
            # Right Row 3
            self.set_font("helvetica", "B", 8)
            self.set_text_color(100, 116, 139)
            self.cell(30, 6, "  Reported On", 0, 0)
            self.set_font("helvetica", "B", 9)
            self.set_text_color(15, 23, 42)
            self.cell(65, 6, f":  {datetime.today().strftime('%d-%b-%Y %I:%M %p')}", 0, 1)
            
            self.ln(2)
            
            # Left Row 4 (Barcode)
            self.set_font("helvetica", "B", 8)
            self.set_text_color(100, 116, 139)
            self.cell(30, 10, "  UID Barcode", 0, 0)
            self.cell(5, 10, ":", 0, 0)
            if self.bc_path:
                self.image(self.bc_path, x=45, y=self.get_y()+2, h=6)
            
            self.cell(60, 10, "", 0, 0) # Spacer
            
            # Right Row 4 (QR Code)
            self.cell(30, 10, "  Verification", 0, 0)
            self.cell(5, 10, ":", 0, 0)
            if self.qr_path:
                self.image(self.qr_path, x=140, y=self.get_y(), h=10)
                
            self.ln(15)
            
        # --- PAGE 2+: COMPRESSED HEADER ---
        else:
            self.set_y(32)
            self.set_font("helvetica", "B", 9)
            self.set_text_color(15, 23, 42)
            self.cell(100, 6, f"Patient: {self.order.get('patient_name', 'N/A').title()} ({self.order.get('patient_uid', '')})", 0, 0, 'L')
            self.cell(90, 6, f"Order: {self.order.get('order_ref', '')}", 0, 1, 'R')
            self.set_draw_color(203, 213, 225)
            self.line(10, self.get_y(), 200, self.get_y())
            self.ln(4)
        
        # --- 5-COLUMN TABLE HEADER ---
        self.set_font("helvetica", "B", 8)
        self.set_fill_color(241, 245, 249)
        self.set_text_color(15, 23, 42)
        self.set_draw_color(203, 213, 225)
        
        self.cell(65, 8, ' INVESTIGATION', 'TB', 0, 'L', True)
        self.cell(20, 8, 'RESULT', 'TB', 0, 'C', True)
        self.cell(20, 8, 'UNIT', 'TB', 0, 'C', True)
        self.cell(45, 8, 'BIO. REF. INTERVAL', 'TB', 0, 'C', True)
        self.cell(40, 8, 'METHOD', 'TB', 1, 'C', True)
        self.ln(2)

    def footer(self):
        self.set_y(-32)
        self.set_draw_color(203, 213, 225)
        self.line(10, 265, 200, 265)
        
        doc_1_name = self.lab.get('doctor_1_name', 'Dr. Ram Shran')
        doc_1_deg = self.lab.get('doctor_1_degree', 'MBBS, MD (Pathology) | DMC-44740')
        doc_2_name = self.lab.get('doctor_2_name', 'Dr. Abdul Sameer Qureshi')
        doc_2_deg = self.lab.get('doctor_2_degree', 'MBBS, D.C.P | DMC-39510')

        self.set_y(-27)
        self.set_font("helvetica", "B", 10)
        self.set_text_color(15, 23, 42)
        self.cell(100, 5, doc_1_name, ln=False, align="L")
        self.cell(90, 5, doc_2_name, ln=True, align="R")
        
        self.set_font("helvetica", "", 8)
        self.set_text_color(100, 116, 139)
        self.cell(100, 4, doc_1_deg, ln=False, align="L")
        self.cell(90, 4, doc_2_deg, ln=True, align="R")
        
        self.set_y(-10)
        self.set_font("helvetica", "I", 8)
        self.cell(0, 5, f"*** End Of Report | Page {self.page_no()} ***", align="C")

def generate_medical_report(order_id, order_data, results_data, lab_data):
    qr = qrcode.QRCode(box_size=4, border=0)
    qr.add_data(f"https://caredrop.in/download-report/{order_id}")
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="#0F172A", back_color="white")
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tf_qr:
        qr_img.save(tf_qr, 'PNG')
        qr_path = tf_qr.name

    bc_img = barcode.get('code128', order_data.get('patient_uid', '0000'), writer=ImageWriter())
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tf_bc:
        bc_path = bc_img.save(tf_bc.name.replace('.png', ''))

    pdf = CareDropPDF(qr_path=qr_path, bc_path=bc_path, order_data=order_data, lab_data=lab_data)
    pdf.add_page()
    
    current_cat, current_test = "", ""
    interpretations_to_print = []

    for r in results_data:
        if r['cat'] != current_cat:
            pdf.ln(4)
            pdf.set_font("helvetica", "BU", 10)
            pdf.set_text_color(13, 148, 136)
            pdf.cell(190, 8, f"DEPARTMENT OF {r['cat'].upper()}", 0, 1, 'C')
            current_cat = r['cat']
            
        if r['test'] != current_test:
            pdf.set_font("helvetica", "B", 10)
            pdf.set_text_color(15, 23, 42)
            pdf.cell(190, 8, r['test'].upper(), 0, 1, 'L')
            current_test = r['test']
            
            if r.get('interpretation'):
                if {'test': r['test'], 'text': r['interpretation']} not in interpretations_to_print:
                    interpretations_to_print.append({'test': r['test'], 'text': r['interpretation']})
        
        p_name = str(r['param'])[:35] + ("..." if len(str(r['param'])) > 35 else "")
        method_str = str(r.get('method') or '')[:25] + ("..." if len(str(r.get('method') or '')) > 25 else "")
        
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(65, 7, f"  {p_name}", 0, 0, 'L')
        
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(20, 7, str(r['val']), 0, 0, 'C')
        
        pdf.set_font("helvetica", "", 9)
        pdf.set_text_color(71, 85, 105)
        pdf.cell(20, 7, str(r['unit']), 0, 0, 'C')
        pdf.cell(45, 7, str(r['ref']), 0, 0, 'C')
        
        pdf.set_font("helvetica", "I", 8)
        pdf.cell(40, 7, method_str, 0, 1, 'C')
        
        pdf.set_draw_color(241, 245, 249)
        pdf.line(12, pdf.get_y(), 198, pdf.get_y())

    if interpretations_to_print:
        pdf.ln(10)
        pdf.set_fill_color(248, 250, 252)
        pdf.set_font("helvetica", "B", 10)
        pdf.set_text_color(15, 23, 42)
        pdf.cell(190, 8, " CLINICAL INTERPRETATIONS", 0, 1, 'L', True)
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
        self.set_fill_color(15, 23, 42)
        self.rect(0, 0, 210, 20, 'F')
        
        self.set_y(5)
        self.set_font("helvetica", "B", 24)
        self.set_text_color(255, 255, 255)
        self.cell(100, 10, "  CAREDROP", ln=False)
        self.set_font("helvetica", "B", 12)
        self.set_text_color(13, 148, 136)
        self.cell(90, 10, "TAX INVOICE / RECEIPT  ", ln=True, align="R")
        self.ln(10)

def generate_invoice_report(order_data, items_data):
    pdf = InvoicePDF()
    pdf.add_page()
    
    pdf.set_y(35)
    pdf.set_draw_color(203, 213, 225)
    pdf.set_line_width(0.3)
    pdf.rect(10, 35, 190, 25)
    self_div = 105
    pdf.line(self_div, 35, self_div, 60)
    
    pdf.set_y(37)
    pdf.set_font("helvetica", "B", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(30, 6, "  Billed To", 0, 0)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(65, 6, f":  {order_data['patient_name']}", 0, 0)
    
    pdf.set_font("helvetica", "B", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(30, 6, "  Invoice No", 0, 0)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(65, 6, f":  {order_data['order_ref']}", 0, 1)

    pdf.set_font("helvetica", "B", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(30, 6, "  Patient UID", 0, 0)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(65, 6, f":  {order_data['patient_uid']}", 0, 0)
    
    pdf.set_font("helvetica", "B", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(30, 6, "  Invoice Date", 0, 0)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    created = order_data.get('created_at') or datetime.today()
    pdf.cell(65, 6, f":  {created.strftime('%d-%b-%Y')}", 0, 1)

    pdf.set_font("helvetica", "B", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(30, 6, "  Age/Gender", 0, 0)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(65, 6, f":  {order_data['age']} / {order_data['gender']}", 0, 0)
    
    pdf.set_font("helvetica", "B", 9)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(30, 6, "  Panel / TPA", 0, 0)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_text_color(15, 23, 42)
    tpa = order_data.get('tpa_name')
    tpa_str = tpa if tpa else "Normal (Self Pay)"
    pdf.cell(65, 6, f":  {tpa_str}", 0, 1)
    
    pdf.ln(12)
    
    pdf.set_font("helvetica", "B", 10)
    pdf.set_fill_color(241, 245, 249)
    pdf.set_text_color(15, 23, 42)
    pdf.cell(15, 10, " S.No", 'TB', 0, "C", True)
    pdf.cell(140, 10, " Description of Investigation", 'TB', 0, "L", True)
    pdf.cell(35, 10, " Amount (INR)", 'TB', 1, "C", True)
    pdf.ln(2)
    
    pdf.set_font("helvetica", "", 10)
    for index, item in enumerate(items_data, 1):
        pdf.cell(15, 8, f" {index}", 0, 0, "C")
        pdf.cell(140, 8, f" {item['test_name'].title()}", 0, 0, "L")
        pdf.cell(35, 8, f" {int(item['price'])}.00", 0, 1, "C")
        pdf.set_draw_color(241, 245, 249)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(2)
        
    pdf.ln(5)
    pdf.set_font("helvetica", "B", 10)
    pdf.set_fill_color(248, 250, 252)
    pdf.set_draw_color(203, 213, 225)
    
    total = int(order_data.get('total_amount') or 0)
    advance = int(order_data.get('advance_amount') or 0)
    balance = int(order_data.get('balance_amount') or 0)
    
    pdf.cell(155, 8, " TOTAL AMOUNT DUE:", 1, 0, "R", True)
    pdf.cell(35, 8, f" Rs. {total}.00", 1, 1, "C", True)
    
    pdf.cell(155, 8, " ADVANCE PAID:", 1, 0, "R", True)
    pdf.cell(35, 8, f" Rs. {advance}.00", 1, 1, "C", True)
    
    pdf.set_text_color(220, 38, 38) if balance > 0 else pdf.set_text_color(22, 163, 74)
    pdf.cell(155, 8, " PENDING BALANCE:", 1, 0, "R", True)
    pdf.cell(35, 8, f" Rs. {balance}.00", 1, 1, "C", True)
    
    return pdf.output()
