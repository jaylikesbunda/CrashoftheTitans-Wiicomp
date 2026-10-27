#include "recompiler/recompiler.h"
#include <fstream>
#include <iostream>
#include <sstream>
int main(int argc,char**argv){
    if(argc!=3)return 2;
    nwii::loader::Executable exe;
    nwii::analyzer::Analyzer analyzer(exe);
    nwii::recomp::Recompiler recomp(analyzer);
    std::ifstream input(argv[1]); std::ofstream out(argv[2]);
    std::string line;
    while(std::getline(input,line)){
        std::istringstream ss(line); unsigned addr,word; ss>>std::hex>>addr;
        nwii::analyzer::Function f; f.start_address=addr;
        while(ss>>std::hex>>word){f.instructions.push_back({addr,word});addr+=4;}
        f.end_address=addr;out<<recomp.generate_function_cpp(f);
    }
}
