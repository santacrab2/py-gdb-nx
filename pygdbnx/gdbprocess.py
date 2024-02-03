"""Wrapper around pygdbmi.GdbController for easier switch connection"""

import struct
from typing import Optional, List, Union
import os.path
import pygdbmi.gdbcontroller
import pygdbmi.constants

from .breakpoint import Breakpoint, Watchpoint
from .exceptions import GDBNotFoundException, WaitApplicationException


class GdbProcess(pygdbmi.gdbcontroller.GdbController):
    """Wrapper around pygdbmi.GdbController for easier switch connection"""
    def __init__(
        self,
        ip_address: str,
        breakpoints: Optional[List[Breakpoint]] = None,
        wait_for_application: bool = False,
        path_to_gdb: Optional[str] = "aarch64-none-elf-gdb.exe",
        time_to_check_for_additional_output_sec: float =
            pygdbmi.constants.DEFAULT_TIME_TO_CHECK_FOR_ADDITIONAL_OUTPUT_SEC,
    ):
        """
        Create new gdb process and connect to the switch

        Args:
            ip_address (str): Local IP address of the Nintendo Switch console

            breakpoints (Optional[List[Breakpoint]], optional): List of breakpoints
            to apply on start of process.

            wait_for_application (bool): Whether or not to hook as soon as the game runs.
            Defaults to False

            path_to_gdb (Optional[str], optional): Path to gdb executable to run.
            Defaults to "aarch64-none-elf-gdb.exe"

            time_to_check_for_additional_output_sec (float, optional): When parsing responses,
            wait this amout of time before exiting (exits before timeout is reached to save time).
            If <= 0, full timeout time is used.
            Defaults to pygdbmi.constants.DEFAULT_TIME_TO_CHECK_FOR_ADDITIONAL_OUTPUT_SEC
        """
        if not os.path.exists(path_to_gdb):
            raise GDBNotFoundException(f"GDB executable not found at {path_to_gdb}."
                                        " Either specify the direct path to gdb,"
                                        " or place it next to the script you are executing")
        super().__init__([path_to_gdb,"--interpreter=mi3"], time_to_check_for_additional_output_sec)
        self.ip_address = ip_address
        self.active_breakpoints = []
        self.main_base: int = None
        self.main_max: int = None
        self.heap_base: int = None
        self.heap_max: int = None
        self.stack_base: int = None
        self.stack_max: int = None
        self.bkpt_no = 1
        self.clear_responses()
        self.connect()
        if wait_for_application:
            self.wait_for_application()
        self.attach()
        self.get_bases()
        self.write("set step-mode on")
        if breakpoints is not None:
            for item in breakpoints:
                self.add_breakpoint(item)

    def wait_for_application(
        self,
    ):
        """
        Hang until a game is run
        """
        response = self.filter_response(self.write("monitor wait application"), "log")
        print("Waiting for application to launch...")
        if len(response) > 1 and "not supported by this target" in response[1]['payload']:
            raise WaitApplicationException("Failed to wait for application, " \
                                           "please restart Nintendo Switch")
        try:
            input("Press Enter when the console is hanging: ")
            self.clear_responses()
        except pygdbmi.constants.GdbTimeoutError as timeout:
            raise WaitApplicationException("Timed out while waiting for application, " \
                                           "please restart Nintendo Switch") from timeout

    def clear_responses(
        self,
    ):
        """
        Clear all cached gdb responses by sending an empty string
        """
        self.write("")

    def resume_execution(
        self,
    ):
        """
        Resume program execution by sending the continue command
        """
        self.write("continue")

    def connect(
        self,
    ):
        """
        Connect to the switch with the ip address stored in self.ip_address
        """
        self.write(f"target extended-remote {self.ip_address}:22225", read_response=False)
        self.log_response(self.wait_for_response())

    def attach(
        self,
        process_name: str = "Application",
    ):
        """
        Attach to process of name process_name

        Args:
            process_name (str, optional): Name of switch process to attach to.
            Defaults to "Application"
        """
        self.write("info os processes", read_response = False)
        processes = self.wait_for_response()
        for line in reversed(processes): # sort by latest process started
            if line['type'] == "console" and process_name in line["payload"]:
                process_id = int(line["payload"].split(" ",1)[0])
                self.log_response(self.write(f"attach {process_id}"))
                break

    def get_bases(
        self,
    ):
        """
        Read the base addresses of sections of the switch's memory
        """
        self.write("monitor get base", read_response = False)
        for line in self.filter_response(self.wait_for_response("target"), "target"):
            if "Heap" in line['payload']:
                self.heap_base, self.heap_max = \
                    (int(num, 16) for num in line['payload'].replace(" -","")[:-2].split(" ")[4:6])
            elif "Stack" in line['payload']:
                self.stack_base, self.stack_max = \
                    (int(num, 16) for num in line['payload'].replace(" -","")[:-2].split(" ")[3:5])
            elif ".nss" in line['payload']:
                self.main_base, self.main_max = \
                    (int(num, 16) for num in line['payload'].replace(" -","")[:-2].split(" ")[2:4])

    def read_instruction(
        self,
        address: int,
        offset_main: Optional[bool] = False,
        offset_heap: Optional[bool] = False,
    ) -> str:
        """
        Read instruction from address

        Args:
            address (int): Address to read from
            offset_main (bool, optional): Whether or not to offset address by
            self.main_base. Defaults to False
            offset_heap (bool, optional): Whether or not to offset address by
            self.heap_base. Defaults to False

        Returns:
            str: Instruction information
        """
        if offset_main:
            address += self.main_base
        elif offset_heap:
            address += self.heap_base
        self.write(f"x/1iw {address}", read_response = False)
        return self.filter_response(
            self.get_gdb_response(),
            "console"
            )[0]['payload'].split(":")[1].replace("\\t","\t").replace("\\n","")

    def read_int(
        self,
        address: int,
        size: str = "g",
        offset_main: Optional[bool] = False,
        offset_heap: Optional[bool] = False,
    ) -> int:
        """
        Read memory at address

        Args:
            address (int): Address to read from
            size (str, optional): GDB size of int to read. Defaults to "g"
            offset_main (bool, optional): Whether or not to offset address by
            self.main_base. Defaults to False
            offset_heap (bool, optional): Whether or not to offset address by
            self.heap_base. Defaults to False

        Returns:
            int: Integer read from address
        """
        if offset_main:
            address += self.main_base
        elif offset_heap:
            address += self.heap_base
        self.write(f"x/1x{size} {address}", read_response = False)
        return int(self.filter_response(
            self.wait_for_response(),
            "console"
            )[0]['payload'].split("0x")[-1][:-1].replace(":",""),16)

    def read_bytes(
        self,
        address: int,
        size: str = "g",
        offset_main: Optional[bool] = False,
        offset_heap: Optional[bool] = False,
    ) -> bytes:
        """
        Read memory at address and convert it to bytes

        Args:
            address (int): Address to read from
            size (str, optional): GDB size of int to read. Defaults to "g"
            offset_main (bool, optional): Whether or not to offset address by
            self.main_base. Defaults to False
            offset_heap (bool, optional): Whether or not to offset address by
            self.heap_base. Defaults to False

        Returns:
            bytes: Bytes read from address
        """
        if size == "b":
            struct_size = "B"
        elif size == "h":
            struct_size = "H"
        elif size == "w":
            struct_size = "I"
        elif size == "g":
            struct_size = "Q"
        return struct.pack(struct_size, self.read_int(address, size, offset_main, offset_heap))

    def read_float(
        self,
        address: int,
        offset_main: Optional[bool] = False,
        offset_heap: Optional[bool] = False,
    ) -> float:
        """
        Read float at address

        Args:
            address (int): Address to read from
            offset_main (bool, optional): Whether or not to offset address by
            self.main_base. Defaults to False
            offset_heap (bool, optional): Whether or not to offset address by
            self.heap_base. Defaults to False

        Returns:
            float: Float read from address
        """
        return struct.unpack("f", self.read_bytes(address, "w", offset_main, offset_heap))[0]

    def write_int(
        self,
        address: int,
        value: int,
        size: str = "g",
        offset_main: Optional[bool] = False,
        offset_heap: Optional[bool] = False,
    ):
        """
        Write integer of size to address

        Args:
            address (int): Address to write to
            value (int): Value to write to memory
            size (str, optional): GDB size of int to write. Defaults to "g"
            offset_main (bool, optional): Whether or not to offset address by
            self.main_base. Defaults to False
            offset_heap (bool, optional): Whether or not to offset address by
            self.heap_base. Defaults to False
        """
        if size == "b":
            size = "unsigned char"
        elif size == "h":
            size = "unsigned short"
        elif size == "w":
            size = "unsigned int"
        elif size == "g":
            size = "unsigned long"
        if offset_main:
            address += self.main_base
        elif offset_heap:
            address += self.heap_base
        self.write(f"set {{{size}}}{address} = {value}")

    def write_bytes(
        self,
        address: int,
        value: bytes,
        size: str = "g",
        offset_main: Optional[bool] = False,
        offset_heap: Optional[bool] = False,
    ):
        """
        Write bytes of size to address

        Args:
            address (int): Address to write to
            value (bytes): Value to write to memory
            size (str, optional): GDB size of bytes to write. Defaults to "g"
            offset_main (bool, optional): Whether or not to offset address by
            self.main_base. Defaults to False
            offset_heap (bool, optional): Whether or not to offset address by
            self.heap_base. Defaults to False
        """
        self.write_int(address, struct.unpack("I", value)[0], size, offset_main, offset_heap)

    def write_float(
        self,
        address: int,
        value: float,
        offset_main: Optional[bool] = False,
        offset_heap: Optional[bool] = False,
    ):
        """
        Write float to address

        Args:
            address (int): Address to write to
            value (float): Value to write to memory
            offset_main (bool, optional): Whether or not to offset address
            by self.main_base. Defaults to False
            offset_heap (bool, optional): Whether or not to offset address
            by self.heap_base. Defaults to False
        """
        self.write_bytes(address, struct.pack("f", value), "w", offset_main, offset_heap)

    def read_current_instruction(
        self,
    ) -> str:
        """
        Read current instruction from program counter

        Returns:
            str: Instruction information
        """
        return self.read_instruction(self.read_register("pc"))

    def read_program_counter(
        self,
    ) -> str:
        """
        Read program counter relative to 0x7100000000

        Returns:
            int: Program counter relative to 0x7100000000
        """
        return 0x7100000000 | self.read_register("pc") - self.main_base

    def read_return_address(
        self,
    ) -> str:
        """
        Read return address (x30) relative to 0x7100000000

        Returns:
            int: Return address (x30) relative to 0x7100000000
        """
        return 0x7100000000 | self.read_register("x30") - self.main_base

    def read_register(
        self,
        register: str,
    ) -> Union[int, float]:
        """
        Read value of register as either an int or a float

        Args:
            register (str): Register to read from

        Returns:
            Union[int, float]: Value read from register
        """
        self.write(f"info register ${register}", read_response = False)
        if register != "sp" and (register.startswith("s") or register.startswith("d")):
            return float(self.filter_response(
                self.get_gdb_response(),
                "console"
                )[0]['payload'].split("f = ")[-1].split(",")[0])
        return int(self.filter_response(
            self.get_gdb_response(),
            "console"
            )[0]['payload'].split(" ")[-1].replace("\n", "").replace("\\n",""), 0)

    def write_register(
        self,
        register: str,
        value: Union[int, float],
        type_string: str = "int",
    ):
        """
        Overwrite register with value

        Args:
            register (str): Register to write to
            value (Union[int, float]): Value to write to register
            type_string (str): C type string to use when writing to register. Defaults to "int"
        """
        self.write(f"set ${register} = ({type_string}){value}")

    def add_breakpoint(
        self,
        bkpt: Breakpoint,
    ):
        """
        Activate breakpoint

        Args:
            bkpt (Breakpoint): Breakpoint object to activate
        """
        bkpt.bkpt_no = self.bkpt_no
        self.active_breakpoints.append(bkpt)
        if isinstance(bkpt, Watchpoint):
            self.write(f"{bkpt.watch_type} * 0x{bkpt.address:X}")
        else:
            self.write(f"b * 0x{self.main_base + (bkpt.address & 0xFFFFFFFF):X}")
        if not bkpt.active:
            self.write(f"disable {bkpt.bkpt_no}")
        self.bkpt_no += 1

    def wait_for_break(
        self,
        timeout: float = 60.0,
    ):
        """
        Wait for and deal with a breakpoint being hit

        Args:
            timeout (float, optional): Time in seconds to wait before timing out. Defaults to 60.0
        """
        response = self.get_gdb_response(timeout_sec = timeout, raise_error_on_timeout = False)
        if response is not None:
            bkpt_hit: Breakpoint = None
            for line in response:
                if line['message'] == "breakpoint-modified":
                    bkpt_info = line['payload']['bkpt']
                    bkpt_no = int(bkpt_info['number'])
                    bkpt_hit = self.active_breakpoints[bkpt_no - 1]
                    break
            if bkpt_hit is not None:
                print(f"Breakpoint at \"{bkpt_hit.name}\" hit")
                if isinstance(bkpt_hit, Watchpoint):
                    access_address: int = None
                    while access_address is None:
                        for line in reversed(response):
                            if "frame" in line['payload']:
                                access_address = int(line['payload']['frame']['addr'],16)
                                break
                        if access_address is None:
                            response = self.get_gdb_response(
                                timeout_sec = timeout,
                                raise_error_on_timeout = False
                            )
                    access_address = 0x7100000000 | (access_address - self.main_base)
                    print(f"Access address: {access_address:X}")
                self.clear_responses()
                if bkpt_hit.on_break is not None:
                    bkpt_hit.on_break(self, bkpt_hit)
                self.resume_execution()
                self.wait_for_break()

    def wait_for_response(
        self,
        target_type: Optional[str] = "console",
        timeout: float = 1.0,
    ) -> List[dict]:
        """
        Wait until response from gdb of type target_type

        Args:
            target_type (Optional[str], optional): mi3 type to wait for. Defaults to "console".
            timeout (float): Amount of seconds to wait each time before timing out. Defaults to 1.0

        Returns:
            List[dict]: First mi3 response line of type target_type
        """
        found = False
        while not found:
            response = self.get_gdb_response(timeout_sec = timeout)
            for line in response:
                if line['type'] == target_type:
                    found = True
                    break
        return response

    def log_response(
        self,
        response: List[dict],
        detailed: Optional[bool] = False,
    ):
        """
        Log a mi3 response List[dict]

        Args:
            response (List[dict]): mi3 response to log
            detailed (bool, optional): Whether or not to log full response. Defaults to False
        """
        if detailed:
            print(response)
        else:
            for line in self.extract_payloads(self.filter_response(response)):
                print(line)

    @staticmethod
    def filter_response(
        response: List[dict],
        target_type: Optional[str] = "console",
    ) -> List[dict]:
        """
        Filter mi3 response down to only that of target_type

        Args:
            response (List[dict]): mi3 response to filter
            target_type (Optional[str], optional): mi3 type to filter for. Defaults to "console"

        Returns:
            List[dict]: Filtered mi3 response
        """
        return [line for line in response if line['type'] == target_type]

    @staticmethod
    def extract_payloads(
        response: List[dict],
    ) -> List[str]:
        """
        Extract only the payloads of a mi3 response

        Args:
            response (List[dict]): mi3 response to extract from

        Returns:
            List[str]: Extracted payloads
        """
        return [line["payload"] for line in response if line["payload"] != ""]
