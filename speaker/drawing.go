package main

import (
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"
)

// Each digit occupies three columns, with one spacer between digits.
func parseDrawing(input string) (out [4]string, err error) {
	input = strings.ReplaceAll(input, "\r\n", "\n")
	input = strings.TrimSuffix(input, "\n")
	rows := strings.Split(input, "\n")
	if len(rows) != 3 {
		return out, errors.New("drawing needs exactly 3 lines, 15 columns: four 3-column digits separated by one space")
	}
	for y, row := range rows {
		if len(row) > 15 {
			return out, fmt.Errorf("line %d exceeds 15 columns", y+1)
		}
		rows[y] = row + strings.Repeat(" ", 15-len(row))
	}
	for y, row := range rows {
		for x, c := range row {
			if c == ' ' {
				continue
			}
			if x%4 == 3 {
				return out, fmt.Errorf("line %d column %d is a separator; use a space", y+1, x+1)
			}
			pos := x % 4
			digit := x / 4
			segment := ""
			if y == 0 && pos == 1 && (c == '_' || c == '-') {
				segment = "A"
			}
			if y == 1 && pos == 0 && c == '|' {
				segment = "F"
			}
			if y == 1 && pos == 1 && (c == '_' || c == '-') {
				segment = "G"
			}
			if y == 1 && pos == 2 && c == '|' {
				segment = "B"
			}
			if y == 2 && pos == 0 && c == '|' {
				segment = "E"
			}
			if y == 2 && pos == 1 && (c == '_' || c == '-') {
				segment = "D"
			}
			if y == 2 && pos == 2 && c == '|' {
				segment = "C"
			}
			if segment == "" {
				return out, fmt.Errorf("invalid drawing character at line %d column %d", y+1, x+1)
			}
			out[digit] += segment
		}
	}
	return out, nil
}

const drawingTemplate = " _   _   _   _ \n|_| |_| |_| |_|\n|_| |_| |_| |_|\n"
const drawingHelp = "Draw four digits in 3 rows, each 3 columns wide, separated by one space.\nUse _ or - for horizontal segments and | for vertical segments.\nSpaces turn segments off. This template lights every segment:\n\n" + drawingTemplate + "\nmini2ctl clock draw --stdin 10 <<'EOF'\n" + drawingTemplate + "EOF\n\nOmitted seconds uses the global temporary display duration; 0 holds until clock clear/reboot.\n"

func drawingCLI(args []string) ([]string, bool, error) {
	if len(args) == 2 && args[0] == "clock" && args[1] == "template" {
		fmt.Print(drawingTemplate)
		return nil, true, nil
	}
	if len(args) >= 3 && args[0] == "clock" && args[1] == "draw" {
		if args[2] == "--help" {
			fmt.Print(drawingHelp)
			return nil, true, nil
		}
		if args[2] == "--stdin" {
			if len(args) > 4 {
				return nil, true, errors.New("clock draw --stdin [seconds]")
			}
			b, e := io.ReadAll(io.LimitReader(os.Stdin, 4097))
			if e != nil {
				return nil, true, e
			}
			if len(b) > 4096 {
				return nil, true, errors.New("drawing exceeds 4096 bytes")
			}
			args[2] = string(b)
		}
	}
	return args, false, nil
}
func drawingCommand(args []string) (any, error, bool) {
	if len(args) < 2 || args[0] != "clock" || args[1] != "draw" {
		return nil, nil, false
	}
	if len(args) != 3 && len(args) != 4 {
		return nil, errors.New("clock draw DRAWING [seconds]; use clock draw --help"), true
	}
	patterns, e := parseDrawing(args[2])
	if e != nil {
		return nil, e, true
	}
	seconds := strconv.Itoa(displaySeconds())
	if len(args) == 4 {
		seconds = args[3]
	}
	if _, e = strconv.Atoi(seconds); e != nil {
		return nil, e, true
	}
	command := []string{"clock", "segments", patterns[0], patterns[1], patterns[2], patterns[3], seconds}
	v, e := dispatch(command)
	return v, e, true
}
