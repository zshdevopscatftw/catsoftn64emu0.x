import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import threading
import time
import random
import os
import struct
import ctypes

# ==========================================
# MIPS R4300i CORE (The "Parallel" Style Backend)
# ==========================================
class MipsCore:
    """
    A pure Python implementation of a MIPS R4300i CPU (N64 Main Processor).
    Architected in the style of a Libretro Core.
    """
    # Register Map (0-31)
    REG_NAMES = [
        "zero", "at", "v0", "v1", "a0", "a1", "a2", "a3",
        "t0", "t1", "t2", "t3", "t4", "t5", "t6", "t7",
        "s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7",
        "t8", "t9", "k0", "k1", "gp", "sp", "fp", "ra"
    ]

    def __init__(self):
        self.regs = [0] * 32
        self.pc = 0x80000400  # Standard N64 Boot Address
        self.memory = bytearray(8 * 1024 * 1024)  # 8MB RDRAM
        self.running = False
        self.paused = False
        self.cycles = 0
        self.instruction_cache = {} # JIT-like cache for decoded opcodes

    def reset(self):
        self.regs = [0] * 32
        self.pc = 0x80000400
        self.cycles = 0
        self.running = False
        print("[MIPS] Core Reset. PC set to 0x80000400")

    def load_binary(self, filepath):
        """Loads a ROM into the emulated RDRAM."""
        try:
            with open(filepath, 'rb') as f:
                data = f.read()
            
            # Simple header check (N64 ROMs start with specific bytes)
            # 0x80371240 is Big Endian (Z64)
            header = data[:4]
            print(f"[LOADER] Header: {header.hex()}")
            
            # Load into "Memory" at 0x00000000 (Physical) / 0x80000000 (Virtual)
            # Truncate to 8MB for this lightweight emulator
            load_size = min(len(data), len(self.memory))
            self.memory[:load_size] = data[:load_size]
            
            # Reset PC to entry point defined in header (usually at offset 0x8)
            entry_point = struct.unpack(">I", data[0x8:0xC])[0]
            self.pc = entry_point
            
            print(f"[LOADER] ROM Loaded. Entry Point: {hex(self.pc)}")
            return True
        except Exception as e:
            print(f"[LOADER] Error: {e}")
            return False

    def step(self):
        """Executes one MIPS instruction."""
        if self.paused: 
            time.sleep(0.01)
            return

        # Fetch
        # Convert Virtual Address (0x80...) to Physical (0x00...)
        phys_addr = self.pc & 0x1FFFFFFF 
        
        # Safety check for memory bounds
        if phys_addr + 4 > len(self.memory):
            # Loop back to start if we run off the end (for demo purposes)
            self.pc = 0x80000400
            phys_addr = self.pc & 0x1FFFFFFF

        # Read 4 bytes (Big Endian)
        try:
            instr_bytes = self.memory[phys_addr:phys_addr+4]
            instr = struct.unpack(">I", instr_bytes)[0]
        except:
            instr = 0 # NOP

        self.pc += 4 # Advance PC

        # Decode & Execute (Simplified MIPS Instruction Set)
        opcode = (instr >> 26) & 0x3F
        
        if opcode == 0x00: # R-Type (Special)
            funct = instr & 0x3F
            if funct == 0x20: # ADD
                self._op_add(instr)
            elif funct == 0x00: # SLL (NOP is SLL 0,0,0)
                pass 
        elif opcode == 0x08: # ADDI
            self._op_addi(instr)
        elif opcode == 0x0F: # LUI
            self._op_lui(instr)
        elif opcode == 0x2B: # SW
            self._op_sw(instr)
        elif opcode == 0x23: # LW
            self._op_lw(instr)
        elif opcode == 0x05: # BNE
            self._op_bne(instr)
        elif opcode == 0x02: # J
            self._op_j(instr)
        else:
            # Unknown opcode, treat as NOP for stability
            pass
            
        self.cycles += 1

    # --- Opcode Implementations ---
    def _op_add(self, instr):
        rs = (instr >> 21) & 0x1F
        rt = (instr >> 16) & 0x1F
        rd = (instr >> 11) & 0x1F
        self.regs[rd] = (self.regs[rs] + self.regs[rt]) & 0xFFFFFFFF

    def _op_addi(self, instr):
        rs = (instr >> 21) & 0x1F
        rt = (instr >> 16) & 0x1F
        imm = ctypes.c_short(instr & 0xFFFF).value # Sign extend
        self.regs[rt] = (self.regs[rs] + imm) & 0xFFFFFFFF

    def _op_lui(self, instr):
        rt = (instr >> 16) & 0x1F
        imm = instr & 0xFFFF
        self.regs[rt] = (imm << 16) & 0xFFFFFFFF

    def _op_sw(self, instr):
        # Store Word (Mock implementation)
        pass 

    def _op_lw(self, instr):
        # Load Word (Mock implementation)
        pass

    def _op_bne(self, instr):
        rs = (instr >> 21) & 0x1F
        rt = (instr >> 16) & 0x1F
        offset = ctypes.c_short(instr & 0xFFFF).value
        if self.regs[rs] != self.regs[rt]:
            self.pc += (offset << 2) - 4 # Branch delay slot handled implicitly by simple step

    def _op_j(self, instr):
        target = instr & 0x03FFFFFF
        self.pc = (self.pc & 0xF0000000) | (target << 2)

# ==========================================
# PARALLEL THREAD CONTROLLER
# ==========================================
class LibretroBackend:
    """
    Manages the MIPS Core in a separate thread, similar to how 
    Libretro cores run asynchronously from the frontend.
    """
    def __init__(self):
        self.core = MipsCore()
        self.thread = None
        self.active = True
        
    def load_rom(self, path):
        if not path: return False
        success = self.core.load_binary(path)
        if success:
            self.core.running = True
        return success

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.active = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.active = False
        self.core.running = False
        if self.thread:
            self.thread.join(timeout=1.0)

    def _run_loop(self):
        """The main emulation loop."""
        print("[BACKEND] Thread started (Parallel Execution Mode)")
        while self.active:
            if self.core.running:
                # Execute a batch of cycles (scanline)
                for _ in range(100): 
                    self.core.step()
                # Yield to let UI breathe (simulating VSync)
                time.sleep(0.001)
            else:
                time.sleep(0.1)

    def get_state_string(self):
        """Returns debug info about registers."""
        return (f"PC: {hex(self.core.pc)}\n"
                f"T0: {hex(self.core.regs[8])}  T1: {hex(self.core.regs[9])}\n"
                f"SP: {hex(self.core.regs[29])}  RA: {hex(self.core.regs[31])}\n"
                f"Cycles: {self.core.cycles}")

# ==========================================
# GUI FRONNTEND (The Tkinter Spaghetti)
# ==========================================
class EmulatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TkN64 - Parallel MIPS Backend")
        self.root.geometry("1024x768")
        self.root.configure(bg="#2b2b2b")

        # Core System (Swapped to new Backend)
        self.backend = LibretroBackend()
        self.backend.start()
        
        self.is_app_running = True

        # Styles
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Treeview", background="#333", fieldbackground="#333", foreground="white")
        style.configure("Treeview.Heading", background="#444", foreground="white")
        
        # Menu Bar
        self.create_menu()

        # Layout: PanedWindow (Splitter)
        self.main_pane = tk.PanedWindow(root, orient=tk.VERTICAL, bg="#2b2b2b", sashwidth=4)
        self.main_pane.pack(fill=tk.BOTH, expand=True)

        # Top Section: Browser + Screen
        self.top_pane = tk.PanedWindow(self.main_pane, orient=tk.HORIZONTAL, bg="#2b2b2b", sashwidth=4)
        self.main_pane.add(self.top_pane, height=600)

        # 1. ROM Browser (Treeview)
        self.browser_frame = tk.Frame(self.top_pane, bg="#2b2b2b")
        self.tree = ttk.Treeview(self.browser_frame, columns=("GoodName", "Region", "Size"), show="headings")
        self.tree.heading("GoodName", text="GoodName")
        self.tree.heading("Region", text="Region")
        self.tree.heading("Size", text="Size")
        self.tree.column("GoodName", width=300)
        self.tree.bind("<Double-1>", self.on_rom_double_click)
        
        # Scrollbar for browser
        scrollbar = ttk.Scrollbar(self.browser_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.top_pane.add(self.browser_frame, width=300)

        # 2. Render Screen (Canvas)
        self.screen_frame = tk.Frame(self.top_pane, bg="black")
        self.canvas = tk.Canvas(self.screen_frame, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        # Overlay Text for MIPS Debug
        self.debug_text = self.canvas.create_text(
            10, 10, text="MIPS R4300i READY", fill="#00FF00", font=("Consolas", 12), anchor="nw"
        )
        
        self.top_pane.add(self.screen_frame, width=724)

        # Bottom Section: Logs
        self.log_frame = tk.Frame(self.main_pane, bg="#1e1e1e")
        self.log_box = scrolledtext.ScrolledText(self.log_frame, bg="#1e1e1e", fg="#00ff00", 
                                                 font=("Consolas", 10), state='disabled')
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self.main_pane.add(self.log_frame, height=168)

        # Status Bar
        self.status_var = tk.StringVar()
        self.status_var.set("Libretro Backend Initialized")
        self.status_bar = tk.Label(root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W, bg="#333", fg="white")
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Add Mock Data
        self.add_mock_rom("Super Mario 64 (U) [!].z64", "NTSC-U", "8MB")
        self.add_mock_rom("Legend of Zelda, The - Ocarina of Time.z64", "NTSC-U", "32MB")
        self.add_mock_rom("GoldenEye 007 (U).z64", "NTSC-U", "12MB")

        # Start the "Render Loop"
        self.animate_static()

    def create_menu(self):
        menubar = tk.Menu(self.root)
        
        # File
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open ROM...", command=self.open_rom_dialog)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close_app)
        menubar.add_cascade(label="File", menu=file_menu)

        # System
        sys_menu = tk.Menu(menubar, tearoff=0)
        sys_menu.add_command(label="Hard Reset", command=self.reset_core)
        sys_menu.add_command(label="Pause/Resume", command=self.toggle_pause)
        menubar.add_cascade(label="System", menu=sys_menu)

        self.root.config(menu=menubar)

    def log(self, msg):
        self.log_box.config(state='normal')
        self.log_box.insert(tk.END, f">> {msg}\n")
        self.log_box.see(tk.END)
        self.log_box.config(state='disabled')

    def add_mock_rom(self, name, region, size):
        self.tree.insert("", tk.END, values=(name, region, size))

    def on_rom_double_click(self, event):
        item = self.tree.selection()[0]
        rom_name = self.tree.item(item, "values")[0]
        self.log(f"Requesting load: {rom_name}")
        # In a real app we need the path, here we just use the name as a mock path if not local
        if os.path.exists(rom_name):
            self.load_rom(rom_name)
        else:
            self.log("ROM file not found in local dir. (Mock Mode active)")
            self.backend.core.running = True # Force mock run
            self.backend.core.pc = 0x80000400 # Reset PC

    def open_rom_dialog(self):
        filename = filedialog.askopenfilename(filetypes=[("N64 ROMs", "*.z64;*.n64;*.v64"), ("All Files", "*.*")])
        if filename:
            self.load_rom(filename)

    def load_rom(self, path):
        self.log(f"Loading Binary: {path}...")
        if self.backend.load_rom(path):
            self.status_var.set(f"Playing: {os.path.basename(path)}")
            self.log("Core: ROM Loaded into RDRAM.")
            self.log(f"Core: Entry Point set to {hex(self.backend.core.pc)}")
        else:
            self.log("Load failed or Mock Mode fallback.")
            self.backend.core.running = True

    def reset_core(self):
        self.backend.core.reset()
        self.log("Core Reset.")

    def toggle_pause(self):
        self.backend.core.paused = not self.backend.core.paused
        state = "Paused" if self.backend.core.paused else "Resumed"
        self.log(f"System {state}")

    def close_app(self):
        self.is_app_running = False
        self.backend.stop()
        self.root.quit()

    def animate_static(self):
        """
        Updates the screen and debug info.
        """
        if not self.is_app_running:
            return

        # Update Debug Overlay with Real MIPS State
        state_info = self.backend.get_state_string()
        self.canvas.itemconfigure(self.debug_text, text=state_info)

        if self.backend.core.running and not self.backend.core.paused:
            # Generate "TV Static" or "Game Output"
            w = self.canvas.winfo_width()
            h = self.canvas.winfo_height()
            
            # Simple "Video Output" simulation (Fast noise)
            if random.random() < 0.2: # Don't redraw every frame to save CPU for MIPS thread
                color = f"#{random.randint(0, 30):02x}{random.randint(0, 30):02x}{random.randint(50, 100):02x}"
                self.canvas.delete("framebuffer")
                self.canvas.create_rectangle(0, 0, w, h, fill=color, outline="", tags="framebuffer")
                self.canvas.tag_raise(self.debug_text)

        self.root.after(30, self.animate_static)

if __name__ == "__main__":
    root = tk.Tk()
    app = EmulatorApp(root)
    # Handle clean exit
    root.protocol("WM_DELETE_WINDOW", app.close_app)
    root.mainloop()
