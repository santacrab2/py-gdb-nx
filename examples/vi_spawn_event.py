"""An example of reading stack information when a breakpoint is hit"""
# pylint: disable=import-error, wrong-import-position, unused-argument
import sys
# exit examples directory
sys.path.append("../")

# TODO: update enums for SV
from pokemonenums import Nature, Species, ITEMS
from pygdbnx.gdbprocess import GdbProcess
from pygdbnx.breakpoint import Breakpoint

def is_shiny(pid: int, sidtid: int):
    """Check if a given pid is shiny"""
    temp = pid ^ sidtid
    return ((temp & 0xFFFF) ^ (temp >> 16)) < 0x10

def overworld_spawn_event(gdbprocess: GdbProcess, bkpt: Breakpoint):
    """Function to be called when a pokemon is generated"""
    try:
        pokemon_addr = gdbprocess.read_register("sp") + 0x18
        try:
            species = Species(gdbprocess.read_int(pokemon_addr + 0x18, "h"))
        except Exception:
            species = gdbprocess.read_int(pokemon_addr + 0x18, "h")
        pid = gdbprocess.read_int(pokemon_addr + 0x8)
        tidsid = gdbprocess.read_int(pokemon_addr + 0x10)
        if(is_shiny(pid,tidsid)):
            print(
                f"{species=}",
                f"shiny = true",
                sep="\n",
                end="\n\n",
            )
            wait = input("press enter to continue")
        else:
            print(
                f"{species=}",
                f"shiny = false")
    except Exception as e:
        return


# IP of switch
gdb_process = GdbProcess("192.168.1.151")
# create breakpoint at address 7100D0AA60
# (near end of pokemon generation function in Violet)
gdb_process.add_breakpoint(Breakpoint(
    0x7100def960,
    "Pokemon Generated",
    on_break = overworld_spawn_event
    ))
# connecting with gdb automatically pauses execution, resume in order to wait for breakpoints
gdb_process.resume_execution()
# start loop of waiting for breaks
gdb_process.wait_for_break()
