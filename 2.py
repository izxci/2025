import streamlit as st
import zipfile
import xml.etree.ElementTree as ET
import re
from pypdf import PdfReader
from io import BytesIO
import google.generativeai as genai
import importlib.metadata
from docx import Document
from fpdf import FPDF # PDF oluşturmak için gerekli
import urllib.parse # WhatsApp linki için

# --- Sayfa Ayarları ---
st.set_page_config(
    page_title="Hukuk Asistanı AI",
    page_icon="⚖️",
    layout="wide"
)

# --- CSS ---
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .kanun-kutusu { 
        background-color: #fff3e0; 
        padding: 15px; 
        border-left: 5px solid #ff9800; 
        border-radius: 5px; 
        margin-bottom: 10px;
        white-space: pre-wrap;
    }
    .ictihat-kutusu {
        background-color: #e3f2fd;
        padding: 15px;
        border-left: 5px solid #2196f3;
        border-radius: 5px;
        margin-bottom: 10px;
    }
    </style>
    """, unsafe_allow_html=True)

# --- YARDIMCI FONKSİYONLAR ---
def parse_udf(file_bytes):
    try:
        with zipfile.ZipFile(file_bytes) as z:
            if 'content.xml' in z.namelist():
                with z.open('content.xml') as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    text_content = [elem.text.strip() for elem in root.iter() if elem.text]
                    return " ".join(text_content)
            return "HATA: UDF içeriği okunamadı."
    except Exception as e:
        return f"HATA: {str(e)}"

def parse_pdf(file_bytes):
    try:
        reader = PdfReader(file_bytes)
        text = "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])
        return text if text.strip() else "UYARI: PDF metin içermiyor (Resim olabilir)."
    except Exception as e:
        return f"HATA: {str(e)}"

def extract_metadata(text):
    if not isinstance(text, str) or text.startswith(("HATA", "UYARI")):
        return {"mahkeme": "-", "esas": "-", "karar": "-", "tarih": "-"}
    
    esas = re.search(r"(?i)Esas\s*No\s*[:\-]?\s*(\d{4}/\d+)", text)
    karar = re.search(r"(?i)Karar\s*No\s*[:\-]?\s*(\d{4}/\d+)", text)
    tarih = re.search(r"(\d{1,2}[./]\d{1,2}[./]\d{4})", text)
    
    mahkeme = "Tespit Edilemedi"
    for line in text.split('\n')[:40]:
        clean = line.strip()
        if ("MAHKEMESİ" in clean.upper() or "DAİRESİ" in clean.upper()) and len(clean) > 5:
            mahkeme = clean
            break
    return {
        "mahkeme": mahkeme,
        "esas": esas.group(1) if esas else "Bulunamadı",
        "karar": karar.group(1) if karar else "Bulunamadı",
        "tarih": tarih.group(1) if tarih else "Bulunamadı"
    }

# --- DOSYA OLUŞTURMA FONKSİYONLARI ---
def create_word_file(text):
    doc = Document()
    for line in text.split('\n'):
        if line.strip():
            doc.add_paragraph(line)
    byte_io = BytesIO()
    doc.save(byte_io)
    byte_io.seek(0)
    return byte_io

def create_udf_file(text):
    root = ET.Element("content")
    body = ET.SubElement(root, "body")
    for line in text.split('\n'):
        p = ET.SubElement(body, "p")
        p.text = line
    xml_str = ET.tostring(root, encoding='utf-8', method='xml')
    byte_io = BytesIO()
    with zipfile.ZipFile(byte_io, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('content.xml', xml_str)
    byte_io.seek(0)
    return byte_io

def create_pdf_file(text):
    # Standart FPDF Türkçe karakterleri desteklemez, bu yüzden karakter değişimi yapıyoruz
    # (Harici font dosyası yüklememek için basit çözüm)
    tr_map = {
        ord('ğ'):'g', ord('Ğ'):'G', ord('ş'):'s', ord('Ş'):'S', 
        ord('ı'):'i', ord('İ'):'I', ord('ç'):'c', ord('Ç'):'C', 
        ord('ü'):'u', ord('Ü'):'U', ord('ö'):'o', ord('Ö'):'O'
    }
    clean_text = text.translate(tr_map)
    
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    # Başlık
    pdf.set_font("Arial", 'B', 14)
    pdf.cell(200, 10, txt="Hukuki Analiz Raporu", ln=1, align='C')
    pdf.ln(10)
    
    # İçerik
    pdf.set_font("Arial", size=11)
    pdf.multi_cell(0, 10, clean_text)
    
    return pdf.output(dest='S').encode('latin-1')

# --- AKILLI AI MOTORU ---
def get_ai_response(prompt, api_key):
    if not api_key: return "Lütfen API Anahtarı giriniz."
    
    genai.configure(api_key=api_key)
    
    candidate_models = ['gemini-1.5-flash', 'gemini-1.5-pro', 'gemini-1.0-pro', 'gemini-pro']
    last_error = ""
    
    for model_name in candidate_models:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            return response.text 
        except Exception as e:
            last_error = str(e)
            continue 

    try:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                try:
                    model = genai.GenerativeModel(m.name)
                    response = model.generate_content(prompt)
                    return response.text
                except:
                    continue
    except:
        pass

    return f"Hata: {last_error}"

# --- ANA UYGULAMA ---
def main():
    st.title("⚖️ Hukuk Asistanı (v3.2)")
    
    try:
        lib_ver = importlib.metadata.version("google-generativeai")
    except:
        lib_ver = "Bilinmiyor"

    # Session State
    if "doc_text" not in st.session_state: st.session_state.doc_text = ""
    if "last_file_id" not in st.session_state: st.session_state.last_file_id = None
    if "messages" not in st.session_state: st.session_state.messages = []
    if "mevzuat_sonuc" not in st.session_state: st.session_state.mevzuat_sonuc = ""
    if "ictihat_sonuc" not in st.session_state: st.session_state.ictihat_sonuc = ""
    if "dilekce_taslak" not in st.session_state: st.session_state.dilekce_taslak = ""
    # YENİ: Bana Sor cevap state'i
    if "soru_cevap" not in st.session_state: st.session_state.soru_cevap = ""

    with st.sidebar:
        st.header("⚙️ Ayarlar")
        api_key = st.text_input("Google Gemini API Key", type="password")
        st.caption(f"Kütüphane Sürümü: {lib_ver}")
        
        st.divider()
        st.header("📁 Dosya Bilgileri")
        input_davaci = st.text_input("Davacı")
        input_davali = st.text_input("Davalı")
        input_mahkeme = st.text_input("Mahkeme")
        input_dosya_no = st.text_input("Dosya No")
        
        if st.button("🗑️ Temizle"):
            st.session_state.doc_text = ""
            st.session_state.last_file_id = None
            st.session_state.messages = []
            st.session_state.dilekce_taslak = ""
            st.session_state.soru_cevap = ""
            st.rerun()

    uploaded_file = st.file_uploader("Dosya Yükle (UDF/PDF)", type=['udf', 'pdf'])

    if uploaded_file and st.session_state.last_file_id != uploaded_file.file_id:
        with st.spinner("Okunuyor..."):
            file_bytes = BytesIO(uploaded_file.getvalue())
            ext = uploaded_file.name.split('.')[-1].lower()
            raw_text = parse_udf(file_bytes) if ext == 'udf' else parse_pdf(file_bytes)
            st.session_state.doc_text = raw_text
            st.session_state.last_file_id = uploaded_file.file_id
            st.session_state.messages = []

    if st.session_state.doc_text.startswith(("HATA", "UYARI")):
        st.warning(st.session_state.doc_text)
    
    auto_data = extract_metadata(st.session_state.doc_text)

    # --- SEKMELER (6. SEKME EKLENDİ) ---
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["📋 Analiz", "💬 Sohbet", "📕 Mevzuat", "⚖️ İçtihat", "✍️ Dilekçe Yaz", "❓ Bana Sor"])

    with tab1:
        col1, col2 = st.columns(2)
        with col1:
            st.write(f"**Mahkeme:** {input_mahkeme or auto_data['mahkeme']}")
            st.write(f"**Dosya No:** {input_dosya_no or auto_data['esas']}")
        with col2:
            st.write(f"**Davacı:** {input_davaci or '-'}")
            st.write(f"**Davalı:** {input_davali or '-'}")
        st.text_area("Metin Önizleme", st.session_state.doc_text, height=150)

    with tab2:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])

        if prompt := st.chat_input("Soru sor..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"): st.markdown(prompt)
            
            with st.chat_message("assistant"):
                with st.spinner("AI Yanıtlıyor..."):
                    context = f"BELGE: {st.session_state.doc_text[:20000]}\nSORU: {prompt}"
                    reply = get_ai_response(f"Sen bir avukatsın. Şuna cevap ver: {context}", api_key)
                    st.markdown(reply)
                    st.session_state.messages.append({"role": "assistant", "content": reply})

    with tab3:
        c1, c2 = st.columns([3,1])
        q = c1.text_input("Kanun Madde No", key="mq")
        if c2.button("Getir", key="mb") and q:
            with st.spinner("Aranıyor..."):
                res = get_ai_response(f"GÖREV: '{q}' maddesini tam metin yaz.", api_key)
                st.session_state.mevzuat_sonuc = res
        if st.session_state.mevzuat_sonuc:
            st.markdown(f"<div class='kanun-kutusu'>{st.session_state.mevzuat_sonuc}</div>", unsafe_allow_html=True)

    with tab4:
        c3, c4 = st.columns([3,1])
        iq = c3.text_input("İçtihat Konusu", key="iq")
        if c4.button("Ara", key="ib") and iq:
            with st.spinner("Taranıyor..."):
                res = get_ai_response(f"GÖREV: '{iq}' hakkında Yargıtay kararlarını özetle.", api_key)
                st.session_state.ictihat_sonuc = res
        if st.session_state.ictihat_sonuc:
            st.markdown(f"<div class='ictihat-kutusu'>{st.session_state.ictihat_sonuc}</div>", unsafe_allow_html=True)

    with tab5:
        st.subheader("✍️ Otomatik Savunma/Cevap Dilekçesi")
        if not st.session_state.doc_text or st.session_state.doc_text.startswith(("HATA", "UYARI")):
            st.info("Dilekçe oluşturmak için önce sol menüden bir dosya yükleyin.")
        else:
            col_d1, col_d2 = st.columns([2, 1])
            with col_d1:
                dilekce_turu = st.selectbox("Dilekçe Türü", ["Cevap Dilekçesi", "İtiraz Dilekçesi", "Beyan Dilekçesi"])
                ozel_talimat = st.text_area("Özel Savunma Stratejisi (Opsiyonel)", placeholder="Örn: Zamanaşımı itirazında bulun...")
            with col_d2:
                st.write("")
                st.write("")
                if st.button("Dilekçeyi Yaz (AI)", type="primary"):
                    if not api_key: st.error("API Key gerekli!")
                    else:
                        with st.spinner("Dilekçe yazılıyor..."):
                            mahkeme = input_mahkeme or auto_data['mahkeme']
                            dosya = input_dosya_no or auto_data['esas']
                            davaci = input_davaci or "Davacı"
                            davali = input_davali or "Davalı"
                            prompt = f"""
                            GÖREV: Aşağıdaki metne dayanarak profesyonel bir {dilekce_turu} yaz.
                            BİLGİLER: Mahkeme: {mahkeme}, Dosya: {dosya}, Davacı: {davaci}, Davalı: {davali}, Ek Talimat: {ozel_talimat}
                            KARŞI TARAFIN DİLEKÇESİ (ÖZET): {st.session_state.doc_text[:20000]}
                            KURALLAR: Resmi Türk hukuk dilekçesi formatında olsun.
                            """
                            res = get_ai_response(prompt, api_key)
                            st.session_state.dilekce_taslak = res

            if st.session_state.dilekce_taslak:
                st.divider()
                st.subheader("📄 Dilekçe Taslağı")
                btn_col1, btn_col2 = st.columns(2)
                word_file = create_word_file(st.session_state.dilekce_taslak)
                with btn_col1:
                    st.download_button("💾 Word Olarak İndir (.docx)", word_file, "Dilekce.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                udf_file = create_udf_file(st.session_state.dilekce_taslak)
                with btn_col2:
                    st.download_button("💾 UDF Olarak İndir (.udf)", udf_file, "Dilekce.udf", "application/zip")
                st.text_area("Dilekçe Metni", st.session_state.dilekce_taslak, height=500)

    # --- YENİ EKLENEN 6. SEKME: BANA SOR ---
    with tab6:
        st.subheader("❓ Hukuki Soru & WhatsApp Paylaşımı")
        st.info("Sorduğunuz soruyu hem Mevzuat hem de Yargıtay İçtihatları ile analiz edip cevaplar.")
        
        col_s1, col_s2 = st.columns([3, 1])
        
        with col_s1:
            kullanici_sorusu = st.text_area("Hukuki Sorunuzu Yazın", height=100, placeholder="Örn: Kiracı kirayı ödemezse tahliye süreci nasıl işler?")
        
        with col_s2:
            telefon_no = st.text_input("WhatsApp No (905xxxxxxxxx)", placeholder="905551234567")
            st.caption("Başında + olmadan 90 ile başlayın.")
            
            if st.button("Analiz Et ve Hazırla", type="primary"):
                if not api_key:
                    st.error("API Key giriniz.")
                elif not kullanici_sorusu:
                    st.warning("Lütfen bir soru yazın.")
                else:
                    with st.spinner("Mevzuat ve İçtihatlar taranıyor..."):
                        prompt = f"""
                        GÖREV: Aşağıdaki hukuki soruyu detaylıca cevapla.
                        SORU: {kullanici_sorusu}
                        
                        KURALLAR:
                        1. İlgili KANUN MADDELERİNİ (Mevzuat) belirt ve açıkla.
                        2. İlgili YARGITAY İÇTİHATLARINDAN (Emsal Kararlar) örnekler ver.
                        3. Sonuç olarak net bir hukuki görüş bildir.
                        4. Dili anlaşılır ve profesyonel olsun.
                        """
                        res = get_ai_response(prompt, api_key)
                        st.session_state.soru_cevap = res

        # Sonuç Ekranı
        if st.session_state.soru_cevap:
            st.divider()
            st.markdown(f"<div class='ictihat-kutusu'><b>💡 Hukuki Görüş:</b><br>{st.session_state.soru_cevap}</div>", unsafe_allow_html=True)
            
            # PDF Oluştur
            pdf_data = create_pdf_file(st.session_state.soru_cevap)
            
            # WhatsApp Linki Oluştur (Metin Paylaşımı)
            # Not: WhatsApp Web API dosya yüklemeye izin vermez, sadece metin gönderir.
            encoded_text = urllib.parse.quote(f"*Hukuki Soru:* {kullanici_sorusu}\n\n*Cevap:*\n{st.session_state.soru_cevap}")
            wa_link = f"https://wa.me/{telefon_no}?text={encoded_text}"
            
            col_btn1, col_btn2 = st.columns(2)
            
            with col_btn1:
                st.download_button(
                    label="📄 Cevabı PDF Olarak İndir",
                    data=pdf_data,
                    file_name="Hukuki_Gorus.pdf",
                    mime="application/pdf"
                )
            
            with col_btn2:
                if telefon_no:
                    st.link_button("📲 Cevabı WhatsApp ile Gönder", wa_link)
                else:
                    st.warning("WhatsApp butonu için telefon no giriniz.")

if __name__ == "__main__":
    main()
