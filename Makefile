all:	pytest-python3.8

clean:
	rm -rf pytest-python3.8
	rm -rf ssh2-python
	rm -rf meld

pytest-python3.8:
	./mkvenv
