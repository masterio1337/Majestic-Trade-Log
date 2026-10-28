import mss
import numpy as np
import cv2
from rapidocr import RapidOCR
import customtkinter as ctk
from datetime import datetime
import sqlite3
import os
import sys
import threading
import ctypes
import keyboard
from PIL import Image, ImageTk

# DPI-осведомленность
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except:
        pass

# --- БАЗА ДАННЫХ ---
appdata_dir = os.path.join(os.getenv('LOCALAPPDATA'), "MajesticTradeLog")
os.makedirs(appdata_dir, exist_ok=True)
DB_NAME = os.path.join(appdata_dir, "transactions.db")

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note TEXT,
            amount REAL,
            is_income INTEGER,
            tax_amount REAL,
            date TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS calibration (
            resolution TEXT PRIMARY KEY,
            left INTEGER,
            top INTEGER,
            width INTEGER,
            height INTEGER
        )
    ''')
    conn.commit()
    conn.close()

def get_calibration(resolution):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT left, top, width, height FROM calibration WHERE resolution = ?', (resolution,))
    row = cursor.fetchone()
    conn.close()
    return row

def save_calibration(resolution, left, top, width, height):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO calibration (resolution, left, top, width, height)
        VALUES (?, ?, ?, ?, ?)
    ''', (resolution, left, top, width, height))
    conn.commit()
    conn.close()

def save_to_db(note, amount, is_income, tax_amount, date_str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO logs (note, amount, is_income, tax_amount, date)
        VALUES (?, ?, ?, ?, ?)
    ''', (note, amount, is_income, tax_amount, date_str))
    conn.commit()
    conn.close()

def load_from_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT id, note, amount, is_income, tax_amount, date FROM logs ORDER BY id ASC')
    rows = cursor.fetchall()
    conn.close()
    return rows

def delete_from_db(log_id):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM logs WHERE id = ?', (log_id,))
    conn.commit()
    conn.close()

def clear_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM logs')
    conn.commit()
    conn.close()

# --- OCR ---
ocr = RapidOCR(params={
    "EngineConfig.onnxruntime.intra_op_num_threads": 4,
    "EngineConfig.onnxruntime.inter_op_num_threads": 2,
    "EngineConfig.onnxruntime.enable_cpu_affinity": False
})
sct = mss.mss()

current_cash = 0
current_bank = 0
total_income = 0.0
total_expense = 0.0

def clean_text_to_number(text):
    if not text: return None
    digits = "".join(c for c in text if c.isdigit())
    return int(digits) if digits else None

def get_current_resolution():
    monitor = sct.monitors[1]
    return f"{monitor['width']}x{monitor['height']}"

# --- ОКНО КАЛИБРОВКИ ---
class CalibrationWindow(ctk.CTkToplevel):
    def __init__(self, parent, on_save_callback):
        super().__init__(parent)
        self.on_save = on_save_callback
        self.title("Калибровка баланса")
        self.geometry("1100x800")
        self.attributes("-topmost", True)
        
        monitor = sct.monitors[1]
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        
        self.scale_factor = min(1050 / img.width, 650 / img.height)
        new_size = (int(img.width * self.scale_factor), int(img.height * self.scale_factor))
        img = img.resize(new_size, Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(img)
        
        top_lbl = ctk.CTkLabel(self, text="ЗАЖМИТЕ ЛКМ И ОБВЕДИТЕ ЦИФРЫ БАЛАНСА", font=("Arial", 14, "bold"), text_color="#FFA500")
        top_lbl.pack(pady=5)
        
        self.canvas = ctk.CTkCanvas(self, width=new_size[0], height=new_size[1], highlightthickness=2, highlightbackground="#555")
        self.canvas.pack(pady=5)
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)
        
        self.rect = None
        self.start_x = None
        self.start_y = None
        
        self.canvas.bind("<ButtonPress-1>", self.on_button_press)
        self.canvas.bind("<B1-Motion>", self.on_move_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_button_release)
        
        self.btn_save = ctk.CTkButton(self, text="✅ Подтвердить выбор", font=("Arial", 14, "bold"), 
                                      height=40, fg_color="#28a745", hover_color="#218838",
                                      state="disabled", command=self.save_selection)
        self.btn_save.pack(pady=10)

    def on_button_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.rect: self.canvas.delete(self.rect)

    def on_move_press(self, event):
        cur_x, cur_y = (event.x, event.y)
        if self.rect: self.canvas.delete(self.rect)
        self.rect = self.canvas.create_rectangle(self.start_x, self.start_y, cur_x, cur_y, outline="red", width=3)

    def on_button_release(self, event):
        if self.rect: self.btn_save.configure(state="normal")

    def save_selection(self):
        if not self.rect: return
        coords = self.canvas.coords(self.rect)
        if len(coords) < 4: return
        
        real_x1 = int(coords[0] / self.scale_factor)
        real_y1 = int(coords[1] / self.scale_factor)
        real_x2 = int(coords[2] / self.scale_factor)
        real_y2 = int(coords[3] / self.scale_factor)
        
        left, top = min(real_x1, real_x2), min(real_y1, real_y2)
        width, height = abs(real_x2 - real_x1), abs(real_y2 - real_y1)
        
        save_calibration(get_current_resolution(), left, top, width, height)
        self.on_save()
        self.destroy()

# --- ОСНОВНОЕ ОКНО ---
class OverlayCalculator(ctk.CTk):
    def __init__(self):
        super().__init__()

        init_db() 

        self.title("Журнал перекупщика")
        self.geometry("400x780+50+50") 
        self.attributes("-topmost", True) 
        self.attributes("-alpha", 0.95) 
        self.overrideredirect(True)       

        try:
            icon_path = os.path.join(sys._MEIPASS, "icon.ico") if hasattr(sys, "_MEIPASS") else "icon.ico"
            self.iconbitmap(icon_path)
        except Exception:
            pass

        # --- ВЕРХНЯЯ ПАНЕЛЬ ---
        self.top_frame = ctk.CTkFrame(self, fg_color="#1f1f1f", corner_radius=0, height=30)
        self.top_frame.pack(fill="x")
        self.top_frame.pack_propagate(False) 

        self.lbl_title = ctk.CTkLabel(self.top_frame, text="Лог (F10-баланс, F9-скрыть)", font=("Arial", 11, "bold"))
        self.lbl_title.pack(side="left", padx=10, pady=5)
        
        self.btn_close = ctk.CTkButton(self.top_frame, text="X", width=30, height=25, fg_color="red", hover_color="darkred", 
                      command=self.close_app)
        self.btn_close.pack(side="right", padx=5, pady=2)
        
        self.btn_hide = ctk.CTkButton(self.top_frame, text="👁️", width=30, height=25, fg_color="#333333", hover_color="#555555", 
                      command=self.hide_window)
        self.btn_hide.pack(side="right", padx=0, pady=2)

        for widget in (self.top_frame, self.lbl_title):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<B1-Motion>", self.do_move)

        # Блок баланса
        self.balance_frame = ctk.CTkFrame(self, fg_color="#2b2b2b")
        self.balance_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_cash = ctk.CTkLabel(self.balance_frame, text="Наличные: $0", font=("Arial", 13), text_color="#00FF00")
        self.lbl_cash.pack(anchor="w", padx=10, pady=2)
        
        self.lbl_bank = ctk.CTkLabel(self.balance_frame, text="Банк: $0", font=("Arial", 13), text_color="#00FF00")
        self.lbl_bank.pack(anchor="w", padx=10, pady=2)

        # Кнопки управления
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.pack(fill="x", padx=10, pady=(0, 5))

        self.btn_refresh = ctk.CTkButton(self.btn_frame, text="🔄 Найти (F10)", font=("Arial", 12, "bold"), 
                                         fg_color="#0078D7", hover_color="#005A9E", height=30, command=self.update_balance_click)
        self.btn_refresh.pack(side="left", fill="x", expand=True, padx=(0, 5))

        self.btn_calibrate = ctk.CTkButton(self.btn_frame, text="🎯 Калибровка", font=("Arial", 12, "bold"), 
                                           fg_color="#9B59B6", hover_color="#8E44AD", height=30, width=110, 
                                           command=self.open_calibration)
        self.btn_calibrate.pack(side="right")

        # --- ИТОГОВАЯ ПЛАШКА ---
        self.total_frame = ctk.CTkFrame(self, fg_color="#1a2a3a", corner_radius=5)
        self.total_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_total = ctk.CTkLabel(self.total_frame, text="Чистый итог: $0", font=("Arial", 16, "bold"), text_color="white")
        self.lbl_total.pack(pady=8)

        # Блок ввода
        self.input_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.input_frame.pack(fill="x", padx=10, pady=5)

        self.entry_note = self.create_input_row("Заметка:", 0)
        self.entry_amount = self.create_input_row("Сумма ($):", 1)
        
        self.type_var = ctk.StringVar(value="Расход")
        self.type_switch = ctk.CTkSegmentedButton(
            self.input_frame, 
            values=["Расход (-)", "Доход (+)"],
            variable=self.type_var,
            font=("Arial", 12, "bold")
        )
        self.type_switch.grid(row=2, column=0, columnspan=2, pady=5, sticky="ew")

        self.tax_var = ctk.BooleanVar(value=True)
        self.chk_tax = ctk.CTkCheckBox(
            self.input_frame, 
            text="Налог 3% (при доходе)", 
            variable=self.tax_var, 
            font=("Arial", 12)
        )
        self.chk_tax.grid(row=3, column=0, columnspan=2, pady=5, sticky="w")

        self.btn_add = ctk.CTkButton(self, text="Добавить запись", font=("Arial", 14, "bold"), 
                                      height=40, command=self.add_transaction)
        self.btn_add.pack(fill="x", padx=10, pady=5)

        self.btn_clear = ctk.CTkButton(self, text="Очистить ВСЮ историю", font=("Arial", 12), fg_color="#555555", hover_color="#333333",
                                      height=30, command=self.clear_log)
        self.btn_clear.pack(fill="x", padx=10, pady=(0, 5))

        # Лог
        self.log_frame = ctk.CTkScrollableFrame(self, fg_color="#141414", label_text="История транзакций")
        self.log_frame.pack(fill="both", expand=True, padx=10, pady=5)

        self.load_history()
        
        # --- ГОРЯЧИЕ КЛАВИШИ ---
        keyboard.add_hotkey('F10', self.update_balance_click)
        keyboard.add_hotkey('F9', self.toggle_window)

    def hide_window(self):
        self.withdraw()

    def toggle_window(self):
        if self.state() == "withdrawn":
            self.deiconify()
            self.attributes("-topmost", True)
        else:
            self.withdraw()

    def open_calibration(self):
        keyboard.unhook_all()
        self.withdraw()
        calib_win = CalibrationWindow(self, self.on_calibration_saved)
        calib_win.grab_set()

    def on_calibration_saved(self):
        self.deiconify()
        keyboard.add_hotkey('F10', self.update_balance_click)
        keyboard.add_hotkey('F9', self.toggle_window)

    def update_balance_click(self):
        self.btn_refresh.configure(text="⏳ Ищу...", fg_color="#FFA500")
        threading.Thread(target=self._bg_scan, daemon=True).start()

    def _bg_scan(self):
        scan_balance_once()
        self.btn_refresh.configure(text="🔄 Найти (F10)", fg_color="#0078D7")
        self.after(10, self.refresh_balance_ui)

    def refresh_balance_ui(self):
        self.lbl_cash.configure(text=f"Наличные: ${current_cash:,}".replace(",", " "))
        self.lbl_bank.configure(text=f"Банк: ${current_bank:,}".replace(",", " "))

    def load_history(self):
        global total_income, total_expense
        total_income = 0.0
        total_expense = 0.0
        rows = load_from_db()
        for widget in self.log_frame.winfo_children():
            widget.destroy()
        
        for row in rows:
            log_id, note, amount, is_income, tax_amount, date_str = row
            self.add_log_entry(log_id, note, amount, bool(is_income), tax_amount, date_str=date_str)
            if is_income: total_income += amount
            else: total_expense += amount
        self.update_total_ui()

    def start_move(self, event):
        self.start_x = event.x_root
        self.start_y = event.y_root
        self.win_x = self.winfo_x()
        self.win_y = self.winfo_y()

    def do_move(self, event):
        delta_x = event.x_root - self.start_x
        delta_y = event.y_root - self.start_y
        self.geometry(f"+{self.win_x + delta_x}+{self.win_y + delta_y}")

    def create_input_row(self, label_text, row):
        lbl = ctk.CTkLabel(self.input_frame, text=label_text, font=("Arial", 12))
        lbl.grid(row=row, column=0, sticky="w", pady=2)
        entry = ctk.CTkEntry(self.input_frame, width=150, height=25, justify="right")
        entry.grid(row=row, column=1, sticky="e", pady=2)
        self.input_frame.grid_columnconfigure(1, weight=1)
        return entry

    def update_total_ui(self):
        global total_income, total_expense
        net_total = total_income - total_expense
        color = "#32CD32" if net_total >= 0 else "#FF3333"
        sign = "+" if net_total > 0 else ""
        self.lbl_total.configure(
            text=f"Чистый итог: {sign}{net_total:,.0f} $".replace(",", " "),
            text_color=color
        )

    def clear_log(self):
        clear_db()
        self.load_history()

    def delete_single_entry(self, log_id):
        """Удаляет конкретную запись из БД и перерисовывает лог"""
        delete_from_db(log_id)
        self.load_history() # Перезагружаем историю, чтобы пересчитать итог

    def add_log_entry(self, log_id, note, amount, is_income, tax_amount=0, date_str=None):
        color = "#32CD32" if is_income else "#FF3333"
        sign = "+" if is_income else "-"
        type_text = "Доход" if is_income else "Расход"
        
        entry_frame = ctk.CTkFrame(self.log_frame, fg_color="#1e1e1e", corner_radius=5)
        entry_frame.pack(fill="x", pady=2, padx=2)

        # Верхняя строка (Заметка + Дата + Кнопка удаления)
        header_frame = ctk.CTkFrame(entry_frame, fg_color="transparent")
        header_frame.pack(fill="x", padx=5, pady=(5, 0))

        if note: 
            lbl_note = ctk.CTkLabel(header_frame, text=note, font=("Arial", 12, "bold"), text_color="#DDDDDD", anchor="w")
            lbl_note.pack(side="left")

        # Кнопка удаления (крестик)
        btn_del = ctk.CTkButton(header_frame, text="✖", width=20, height=20, fg_color="transparent", 
                                hover_color="#662222", text_color="#FF6666", font=("Arial", 12),
                                command=lambda: self.delete_single_entry(log_id))
        btn_del.pack(side="right", padx=2)

        display_date = date_str if date_str else datetime.now().strftime("%d.%m %H:%M")
        lbl_date = ctk.CTkLabel(header_frame, text=display_date, font=("Arial", 10), text_color="#888888")
        lbl_date.pack(side="right", padx=5)

        # Нижняя строка (Тип + Сумма)
        data_frame = ctk.CTkFrame(entry_frame, fg_color="transparent")
        data_frame.pack(fill="x", padx=5, pady=(0, 5))

        lbl_type = ctk.CTkLabel(data_frame, text=type_text, font=("Arial", 11), text_color=color, width=60, anchor="w")
        lbl_type.pack(side="left")

        lbl_amount = ctk.CTkLabel(data_frame, text=f"{sign} {amount:,.0f} $".replace(",", " "), font=("Arial", 13, "bold"), text_color=color)
        lbl_amount.pack(side="right")
        
        if tax_amount > 0:
            lbl_tax = ctk.CTkLabel(data_frame, text=f"(налог: -{tax_amount:,.0f} $)".replace(",", " "), font=("Arial", 10), text_color="#AAAAAA")
            lbl_tax.pack(side="right", padx=5)

    def add_transaction(self):
        try:
            note_text = self.entry_note.get().strip() or "Без заметки"
            amount = float(self.entry_amount.get() or 0)
            if amount <= 0: return

            is_income = (self.type_var.get() == "Доход (+)")
            tax_amount = 0
            final_amount = amount

            if is_income and self.tax_var.get():
                tax_amount = amount * 0.03
                final_amount = amount - tax_amount

            now_str = datetime.now().strftime("%d.%m %H:%M")
            save_to_db(note_text, final_amount, 1 if is_income else 0, tax_amount, now_str)
            
            self.entry_note.delete(0, 'end')
            self.entry_amount.delete(0, 'end')
            
            # Перезагружаем историю, чтобы обновить ID и пересчитать итог
            self.load_history() 

        except ValueError:
            pass 

    def close_app(self):
        keyboard.unhook_all()
        self.destroy()

# --- СКАНИРОВАНИЕ ---
def scan_balance_once():
    global current_cash, current_bank
    try:
        resolution = get_current_resolution()
        calib = get_calibration(resolution)
        if not calib:
            print(f"[!] Нет калибровки для {resolution}")
            return
        
        left, top, width, height = calib
        region = {"top": top, "left": left, "width": width, "height": height}
        
        img = np.array(sct.grab(region))
        gray = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2GRAY)
        result = ocr(gray)
        
        debug_texts = []
        if hasattr(result, 'txts') and result.txts:
            debug_texts = [t for t in result.txts if t.strip()]
        elif isinstance(result, list):
            for res in result:
                if isinstance(res, tuple) and len(res) >= 2:
                    debug_texts.append(res[1])

        found_numbers = [clean_text_to_number(t) for t in debug_texts if clean_text_to_number(t) and clean_text_to_number(t) > 100]
        
        if len(found_numbers) >= 2:
            found_numbers.sort(reverse=True)
            current_cash, current_bank = found_numbers[0], found_numbers[1]
        elif len(found_numbers) == 1:
            current_cash, current_bank = found_numbers[0], 0
    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    app = OverlayCalculator()
    app.mainloop()