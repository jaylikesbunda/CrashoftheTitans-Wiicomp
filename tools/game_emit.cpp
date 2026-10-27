// Full-port generator. Imports authoritative function extents, never admissions.
#include "recompiler/recompiler.h"
#include <fstream>
#include <iostream>
#include <sstream>
int main(int argc,char**argv){
    if(argc!=5){std::cerr<<"game_emit DOL index.tsv runtime output\n";return 2;}
    nwii::loader::Executable exe;if(!exe.load_dol(argv[1]))return 3;
    nwii::analyzer::Analyzer analyzer(exe);
    std::ifstream file(argv[2]);std::string line;std::getline(file,line);
    std::map<uint32_t,nwii::analyzer::Function> functions;
    while(std::getline(file,line)){
        std::istringstream ss(line);std::string a,s,name;
        if(!std::getline(ss,a,'\t')||!std::getline(ss,s,'\t'))continue;
        uint32_t addr=std::stoul(a,nullptr,16),size=std::stoul(s,nullptr,0);
        nwii::analyzer::Function f{};f.start_address=addr;f.end_address=addr+size;
        for(uint32_t pc=addr;pc<addr+size;pc+=4){
            bool found=false;
            for(const auto& sec:exe.sections){
                if(sec.is_text&&pc>=sec.address&&pc+4<=sec.address+sec.size){
                    size_t o=pc-sec.address;const auto*b=sec.data.data()+o;
                    uint32_t word=uint32_t(b[0])<<24|uint32_t(b[1])<<16|uint32_t(b[2])<<8|b[3];
                    f.instructions.push_back({pc,word});found=true;break;
                }
            }
            if(!found){std::cerr<<"Unmapped function extent\n";return 4;}
        }
        functions.emplace(addr,std::move(f));
    }
    analyzer.set_imported_functions(std::move(functions));
    nwii::recomp::RecompilerConfig config;
    config.project_name="CrashNative";config.runtime_source_dir=argv[3];config.output_dir=argv[4];
    config.split_output=true;config.instructions_per_file=12000;
    std::cout<<"Imported "<<analyzer.get_functions().size()<<" authoritative function extents\n";
    nwii::recomp::Recompiler recomp(analyzer,nullptr,config);
    return recomp.generate_cmake_project(exe.entry_point)?0:5;
}
